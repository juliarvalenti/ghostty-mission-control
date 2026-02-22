"""
Process discovery for Ghostty terminal sessions.

Finds Ghostty processes, their child shells, working directories,
and detects Claude Code sessions.
"""

import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Matches cloud/remote session IDs like session_01HKb1vQuPyz4rqgyrD51XMP
SESSION_ID_RE = re.compile(r"session_[0-9a-zA-Z]{10,40}")
# Matches local UUID-style session IDs like b4be5e28-17b6-412d-9fd2-fd27dc499bfe
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SHELLS = {"zsh", "bash", "fish", "sh", "tcsh", "csh", "dash", "ksh", "-zsh", "-bash", "-fish", "-sh"}
HOME = str(Path.home())


@dataclass
class TerminalSession:
    """A terminal session in a Ghostty window or tab."""

    shell_pid: int
    tty: str
    shell: str
    cwd: str = ""
    foreground_cmd: str = ""
    foreground_pid: int = 0
    claude_running: bool = False
    claude_session_id: Optional[str] = None
    window_index: Optional[int] = None
    window_title: Optional[str] = None


def _run(cmd: list[str], timeout: int = 5) -> str:
    """Run a command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _build_process_table() -> tuple[dict, dict[int, list[int]]]:
    """
    Build process info table and parent->children mapping from ps output.

    Returns:
        (processes, children) where:
        processes = {pid: {'ppid', 'tty', 'comm'}}
        children = {ppid: [child_pid, ...]}
    """
    output = _run(["ps", "-axo", "pid=,ppid=,tty=,comm="])
    processes: dict[int, dict] = {}
    children: dict[int, list[int]] = defaultdict(list)

    for line in output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue

        tty = parts[2]
        comm = parts[3].strip()
        comm_name = os.path.basename(comm)

        processes[pid] = {
            "ppid": ppid,
            "tty": tty,
            "comm": comm_name,
        }
        children[ppid].append(pid)

    return processes, children


def _get_process_args(pid: int) -> str:
    """Get full command-line arguments for a process."""
    return _run(["ps", "-o", "args=", "-p", str(pid)]).strip()


def _abbreviate_home(path: str) -> str:
    """Replace home directory prefix with ~."""
    if path.startswith(HOME):
        return "~" + path[len(HOME):]
    return path


def _get_cwds(pids: list[int]) -> dict[int, str]:
    """Get current working directories for multiple PIDs using lsof."""
    if not pids:
        return {}

    pid_str = ",".join(str(p) for p in pids)
    output = _run(["lsof", "-a", "-d", "cwd", "-p", pid_str, "-Fn"], timeout=10)

    cwds: dict[int, str] = {}
    current_pid = None
    for line in output.split("\n"):
        if line.startswith("p"):
            try:
                current_pid = int(line[1:])
            except ValueError:
                current_pid = None
        elif line.startswith("n") and current_pid is not None:
            cwds[current_pid] = _abbreviate_home(line[1:])

    return cwds


def _find_descendants(pid: int, children: dict[int, list[int]]) -> list[int]:
    """Find all descendant PIDs of a process (breadth-first)."""
    result = []
    stack = list(children.get(pid, []))
    while stack:
        child = stack.pop()
        result.append(child)
        stack.extend(children.get(child, []))
    return result


def _detect_claude_code(
    shell_pid: int,
    descendants: list[int],
    processes: dict,
    shell_cwd: str,
) -> tuple[bool, Optional[str]]:
    """
    Check if Claude Code is running among the descendant processes.

    Searches for the session ID in:
    1. Process command-line arguments
    2. Process environment variables (via ps eww)
    3. State files in ~/.claude/projects/

    Returns:
        (is_running, session_id_or_None)
    """
    claude_pids: list[int] = []

    for pid in descendants:
        info = processes.get(pid, {})
        comm = info.get("comm", "").lower()
        # Direct match: process is called 'claude' or 'environment-manager'
        if "claude" in comm:
            claude_pids.append(pid)
        elif comm == "environment-manager":
            # environment-manager with --session flag is a Claude Code companion
            args = _get_process_args(pid)
            if "--session" in args:
                claude_pids.append(pid)
        # Node.js process that might be Claude Code
        elif comm == "node":
            args = _get_process_args(pid)
            if "claude" in args.lower():
                claude_pids.append(pid)

    if not claude_pids:
        return False, None

    # Try to extract session ID from command-line args
    for pid in claude_pids:
        args = _get_process_args(pid)
        match = SESSION_ID_RE.search(args)
        if match:
            return True, match.group(0)

    # Try environment variables (macOS: ps eww shows env)
    for pid in claude_pids:
        env_output = _run(["ps", "eww", "-p", str(pid)])
        match = SESSION_ID_RE.search(env_output)
        if match:
            return True, match.group(0)

    # Try reading Claude state files
    session_id = _search_claude_state_files(shell_cwd)
    if session_id:
        return True, session_id

    return True, None


def _search_claude_state_files(cwd: str) -> Optional[str]:
    """
    Search for Claude Code session ID in state files.

    Claude Code stores session transcripts at:
        ~/.claude/projects/<encoded-path>/<UUID>.jsonl

    The path encoding replaces '/' with '-', so /home/user/myapp
    becomes -home-user-myapp.

    For active sessions, we find the most recently modified .jsonl
    file and return its UUID as the session identifier.
    """
    full_cwd = cwd.replace("~", HOME, 1) if cwd.startswith("~") else cwd

    # Encode the CWD path the way Claude Code does: replace / with -
    encoded_path = full_cwd.replace("/", "-")
    project_dir = os.path.join(HOME, ".claude", "projects", encoded_path)

    if os.path.isdir(project_dir):
        session_id = _find_latest_session_in_dir(project_dir)
        if session_id:
            return session_id

    # Also check sessions-index.json if it exists
    index_file = os.path.join(project_dir, "sessions-index.json")
    if os.path.isfile(index_file):
        session_id = _parse_sessions_index(index_file)
        if session_id:
            return session_id

    return None


def _find_latest_session_in_dir(directory: str) -> Optional[str]:
    """
    Find the most recently modified .jsonl session transcript
    and return its UUID filename as the session ID.
    """
    try:
        jsonl_files = []
        for entry in os.listdir(directory):
            if entry.endswith(".jsonl"):
                filepath = os.path.join(directory, entry)
                try:
                    mtime = os.path.getmtime(filepath)
                    jsonl_files.append((mtime, entry))
                except OSError:
                    continue

        if not jsonl_files:
            return None

        # Most recently modified transcript is likely the active session
        jsonl_files.sort(reverse=True)
        latest = jsonl_files[0][1]
        # The filename without .jsonl is the session UUID
        uuid = latest.removesuffix(".jsonl")
        if UUID_RE.fullmatch(uuid):
            return uuid
    except OSError:
        pass
    return None


def _parse_sessions_index(index_file: str) -> Optional[str]:
    """Parse sessions-index.json for the most recent session ID."""
    import json

    try:
        with open(index_file, "r") as f:
            data = json.load(f)
        # sessions-index.json may be a dict or list of session metadata
        if isinstance(data, dict):
            # Try to find the most recent session
            for key in data:
                match = UUID_RE.search(key)
                if match:
                    return match.group(0)
                match = SESSION_ID_RE.search(str(data[key]))
                if match:
                    return match.group(0)
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def _find_foreground(
    shell_pid: int,
    descendants: list[int],
    processes: dict,
    children: dict[int, list[int]],
) -> tuple[str, int]:
    """
    Find the foreground process for a shell session.

    Walks the process tree to find the deepest meaningful child,
    which is typically the foreground process.

    Returns:
        (command_name, pid)
    """
    if not descendants:
        info = processes.get(shell_pid, {})
        return info.get("comm", "?"), shell_pid

    # Build local children map for descendants only
    desc_set = set(descendants)
    desc_set.add(shell_pid)

    def find_leaf(pid: int, depth: int = 0) -> tuple[int, int]:
        """Find the deepest leaf process. Returns (pid, depth)."""
        if depth > 20:  # Safety limit
            return pid, depth

        kids = [c for c in children.get(pid, []) if c in desc_set]
        if not kids:
            return pid, depth

        # Among children, prefer non-helper processes
        best_pid, best_depth = pid, depth
        skip_comms = {"sleep", "cat", "wc", "tee", "grep"}
        for kid in kids:
            comm = processes.get(kid, {}).get("comm", "").lower()
            if comm in skip_comms:
                continue
            leaf_pid, leaf_depth = find_leaf(kid, depth + 1)
            if leaf_depth > best_depth:
                best_pid, best_depth = leaf_pid, leaf_depth

        # If all children were skipped, try the first one anyway
        if best_pid == pid and kids:
            best_pid, best_depth = find_leaf(kids[0], depth + 1)

        return best_pid, best_depth

    leaf_pid, _ = find_leaf(shell_pid)
    if leaf_pid != shell_pid:
        info = processes.get(leaf_pid, {})
        return info.get("comm", "?"), leaf_pid

    info = processes.get(shell_pid, {})
    return info.get("comm", "?"), shell_pid


def discover_sessions() -> list[TerminalSession]:
    """
    Discover all Ghostty terminal sessions.

    Finds the Ghostty process(es), locates child shells,
    determines CWDs, and detects Claude Code sessions.

    Returns:
        List of TerminalSession objects, one per terminal session.
    """
    processes, children = _build_process_table()

    # Find Ghostty main process(es)
    ghostty_pids = []
    for pid, info in processes.items():
        comm = info["comm"].lower()
        if comm == "ghostty":
            ghostty_pids.append(pid)

    if not ghostty_pids:
        return []

    # Find shell processes that are direct (or near-direct) children of Ghostty
    shell_pids: list[int] = []
    intermediary_comms = {"ghostty", "login", "launchd", "sshd", "su", "sudo"}

    for gpid in ghostty_pids:
        all_descendants = _find_descendants(gpid, children)
        for desc_pid in all_descendants:
            info = processes.get(desc_pid, {})
            comm = info.get("comm", "").lower().lstrip("-")
            if comm not in SHELLS:
                continue

            # Check that the parent is Ghostty or a known intermediary
            ppid = info.get("ppid", 0)
            parent_comm = processes.get(ppid, {}).get("comm", "").lower()
            if parent_comm in intermediary_comms:
                shell_pids.append(desc_pid)

    # Deduplicate (in case of overlapping trees)
    shell_pids = list(dict.fromkeys(shell_pids))

    if not shell_pids:
        # Fallback: any shell that is a direct child of any ghostty PID
        for gpid in ghostty_pids:
            for child_pid in children.get(gpid, []):
                info = processes.get(child_pid, {})
                comm = info.get("comm", "").lower().lstrip("-")
                if comm in SHELLS:
                    shell_pids.append(child_pid)

    # Get CWDs in batch
    cwds = _get_cwds(shell_pids)

    # Build sessions
    sessions: list[TerminalSession] = []
    for shell_pid in shell_pids:
        info = processes.get(shell_pid, {})
        descendants = _find_descendants(shell_pid, children)
        cwd = cwds.get(shell_pid, "?")

        # Detect Claude Code
        claude_running, session_id = _detect_claude_code(
            shell_pid, descendants, processes, cwd
        )

        # Find foreground process
        fg_cmd, fg_pid = _find_foreground(shell_pid, descendants, processes, children)

        session = TerminalSession(
            shell_pid=shell_pid,
            tty=info.get("tty", "?"),
            shell=info.get("comm", "?").lstrip("-"),
            cwd=cwd,
            foreground_cmd=fg_cmd.lstrip("-"),
            foreground_pid=fg_pid,
            claude_running=claude_running,
            claude_session_id=session_id,
        )
        sessions.append(session)

    # Sort by PID for stable ordering
    sessions.sort(key=lambda s: s.shell_pid)
    return sessions
