"""
Preview data fetching for terminal sessions.

Provides context for the split-pane preview:
- Claude Code sessions: last user prompt, todos, git branch, recent activity
- Regular shells: terminal content via Ghostty AppleScript (if available)
"""

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ghostty_mc.discovery import SESSION_ID_RE, UUID_RE, TerminalSession

HOME = str(Path.home())


@dataclass
class PreviewData:
    """Preview content for a terminal session."""

    # Common
    lines: list[str] = field(default_factory=list)

    # Claude Code specific
    last_user_prompt: str = ""
    last_assistant_text: str = ""
    todos: list[dict] = field(default_factory=list)
    git_branch: str = ""
    model: str = ""

    # Shell specific
    terminal_content: str = ""


def get_preview(session: TerminalSession) -> PreviewData:
    """
    Fetch preview data for a terminal session.

    For Claude Code sessions: reads transcript and todo files.
    For regular shells: tries to get terminal content via AppleScript.
    """
    preview = PreviewData()

    if session.claude_running and session.claude_session_id:
        _fill_claude_preview(session, preview)
    elif session.window_index is not None:
        _fill_shell_preview(session, preview)

    # Build display lines
    preview.lines = _format_preview(session, preview)
    return preview


def _fill_claude_preview(session: TerminalSession, preview: PreviewData) -> None:
    """Fill preview with Claude Code session data from transcript files."""
    session_id = session.claude_session_id
    if not session_id:
        return

    full_cwd = session.cwd.replace("~", HOME, 1) if session.cwd.startswith("~") else session.cwd
    encoded_path = full_cwd.replace("/", "-")
    project_dir = os.path.join(HOME, ".claude", "projects", encoded_path)

    # Determine which file to read
    transcript_file = None
    if UUID_RE.fullmatch(session_id):
        # Local UUID — transcript is <UUID>.jsonl
        candidate = os.path.join(project_dir, f"{session_id}.jsonl")
        if os.path.isfile(candidate):
            transcript_file = candidate
    if not transcript_file:
        # Try to find the most recent .jsonl in the project dir
        transcript_file = _find_latest_jsonl(project_dir)

    if transcript_file:
        _parse_transcript(transcript_file, preview)

    # Load todos
    _load_todos(session_id, preview)


def _find_latest_jsonl(directory: str) -> Optional[str]:
    """Find the most recently modified .jsonl file in a directory."""
    try:
        best_mtime = 0.0
        best_file = None
        for entry in os.listdir(directory):
            if entry.endswith(".jsonl"):
                filepath = os.path.join(directory, entry)
                try:
                    mtime = os.path.getmtime(filepath)
                    if mtime > best_mtime:
                        best_mtime = mtime
                        best_file = filepath
                except OSError:
                    continue
        return best_file
    except OSError:
        return None


def _parse_transcript(filepath: str, preview: PreviewData) -> None:
    """
    Parse a Claude Code transcript JSONL file.

    Reads the last portion of the file to extract:
    - Last user prompt (non-sidechain, with text content)
    - Last assistant response text
    - Git branch
    - Model info
    """
    try:
        # Read last ~50KB to get recent messages without loading the whole file
        file_size = os.path.getsize(filepath)
        read_from = max(0, file_size - 50_000)

        with open(filepath, "r", errors="replace") as f:
            if read_from > 0:
                f.seek(read_from)
                f.readline()  # Skip partial first line
            tail_lines = f.readlines()

        last_user_prompt = ""
        last_assistant_text = ""
        git_branch = ""
        model = ""

        for line in tail_lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Skip sidechain (subagent) messages
            if obj.get("isSidechain"):
                continue

            msg_type = obj.get("type", "")
            message = obj.get("message", {})
            if not isinstance(message, dict):
                continue

            # Extract git branch
            if obj.get("gitBranch"):
                git_branch = obj["gitBranch"]

            content = message.get("content", "")

            if msg_type == "user":
                text = _extract_text(content)
                # Skip tool results and system messages
                if text and not text.startswith("<task-notification") and not text.startswith("Stop hook"):
                    last_user_prompt = text

            elif msg_type == "assistant":
                text = _extract_text(content)
                if text:
                    last_assistant_text = text

                # Check for model info
                if message.get("model"):
                    model = message["model"]

        preview.last_user_prompt = last_user_prompt
        preview.last_assistant_text = last_assistant_text
        preview.git_branch = git_branch
        preview.model = model

    except OSError:
        pass


def _extract_text(content) -> str:
    """Extract text from message content (string or content block list)."""
    if isinstance(content, str):
        return content.strip()
    elif isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", "").strip())
        return "\n".join(texts)
    return ""


def _load_todos(session_id: str, preview: PreviewData) -> None:
    """Load todo list for a Claude Code session."""
    todos_dir = os.path.join(HOME, ".claude", "todos")
    if not os.path.isdir(todos_dir):
        return

    # Todo files are named: <UUID>-agent-<UUID>.json
    try:
        for entry in os.listdir(todos_dir):
            if session_id in entry and entry.endswith(".json"):
                filepath = os.path.join(todos_dir, entry)
                try:
                    with open(filepath, "r") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        preview.todos = data
                except (OSError, json.JSONDecodeError):
                    pass
                return
    except OSError:
        pass


def _fill_shell_preview(session: TerminalSession, preview: PreviewData) -> None:
    """
    Try to get terminal content via Ghostty AppleScript.

    Falls back gracefully if the AppleScript dictionary is not available.
    """
    if session.window_index is None:
        return

    script = f"""\
try
    tell application "Ghostty"
        set termContent to contents of terminal {session.window_index}
        return termContent
    end tell
on error
    return ""
end try"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        content = result.stdout.strip()
        if content:
            preview.terminal_content = content
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass


def _format_preview(session: TerminalSession, preview: PreviewData) -> list[str]:
    """Format preview data into display lines."""
    lines: list[str] = []

    if session.claude_running:
        _format_claude_preview(session, preview, lines)
    elif preview.terminal_content:
        _format_terminal_preview(session, preview, lines)
    else:
        _format_basic_preview(session, lines)

    return lines


def _format_claude_preview(
    session: TerminalSession, preview: PreviewData, lines: list[str]
) -> None:
    """Format Claude Code session preview."""
    lines.append(f"Claude Code  {session.cwd}")

    if preview.git_branch:
        lines.append(f"Branch: {preview.git_branch}")
    if session.claude_session_id:
        lines.append(f"Session: {session.claude_session_id}")
    if preview.model:
        # Show just the model name, not the full ID
        model_short = preview.model.split("/")[-1] if "/" in preview.model else preview.model
        lines.append(f"Model: {model_short}")

    lines.append("")

    # Todos
    if preview.todos:
        lines.append("Tasks:")
        for todo in preview.todos:
            status = todo.get("status", "?")
            content = todo.get("content", "?")
            if status == "completed":
                marker = "[x]"
            elif status == "in_progress":
                marker = "[>]"
            else:
                marker = "[ ]"
            lines.append(f"  {marker} {content}")
        lines.append("")

    # Last user prompt
    if preview.last_user_prompt:
        lines.append("Last prompt:")
        # Wrap and truncate
        prompt_lines = preview.last_user_prompt.split("\n")
        for pl in prompt_lines[:5]:
            lines.append(f"  {pl[:120]}")
        if len(prompt_lines) > 5:
            lines.append(f"  ... ({len(prompt_lines) - 5} more lines)")
        lines.append("")

    # Last assistant text
    if preview.last_assistant_text:
        lines.append("Last response:")
        resp_lines = preview.last_assistant_text.split("\n")
        for rl in resp_lines[:5]:
            lines.append(f"  {rl[:120]}")
        if len(resp_lines) > 5:
            lines.append(f"  ... ({len(resp_lines) - 5} more lines)")


def _format_terminal_preview(
    session: TerminalSession, preview: PreviewData, lines: list[str]
) -> None:
    """Format terminal content preview."""
    lines.append(f"{session.shell}  {session.cwd}")
    lines.append("")

    # Show last N lines of terminal content
    term_lines = preview.terminal_content.split("\n")
    # Show the last 15 lines (most relevant)
    display_lines = term_lines[-15:] if len(term_lines) > 15 else term_lines
    for tl in display_lines:
        lines.append(tl[:120])


def _format_basic_preview(session: TerminalSession, lines: list[str]) -> None:
    """Format basic preview when no rich data is available."""
    lines.append(f"{session.shell}  {session.cwd}")
    lines.append("")
    lines.append(f"PID: {session.shell_pid}")
    lines.append(f"TTY: {session.tty}")
    if session.foreground_cmd != session.shell:
        lines.append(f"Running: {session.foreground_cmd} (PID {session.foreground_pid})")
    if session.window_title:
        lines.append(f"Window: {session.window_title}")
    lines.append("")
    lines.append("(Terminal content preview requires ghostty-applescript)")
