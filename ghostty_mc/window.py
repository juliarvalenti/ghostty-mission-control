"""
macOS window management for Ghostty.

Uses multiple strategies to enumerate and raise Ghostty windows:
1. Ghostty's native AppleScript dictionary (community project / App Intents)
   - Exposes terminal index, UUID, title, working directory
2. System Events GUI scripting (fallback)
   - Enumerates windows by title, raises via AXRaise
"""

import os
import subprocess
from typing import Optional

from ghostty_mc.discovery import TerminalSession


def _osascript(script: str, timeout: int = 5) -> str:
    """Run an AppleScript and return stdout."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def get_ghostty_windows() -> list[dict]:
    """
    Enumerate Ghostty windows/terminals.

    Tries Ghostty's native AppleScript dictionary first (available with
    the ghostty-applescript community project or Ghostty 1.2+ App Intents).
    Falls back to System Events GUI scripting.

    Returns a list of dicts with keys:
        - index: 1-based terminal/window index
        - title: window/terminal title
        - cwd: working directory (only from native API, else None)
    """
    # Strategy 1: Try Ghostty's native AppleScript (richer data)
    windows = _get_windows_native()
    if windows:
        return windows

    # Strategy 2: Fall back to System Events
    return _get_windows_system_events()


def _get_windows_native() -> list[dict]:
    """
    Try to enumerate terminals via Ghostty's native AppleScript dictionary.

    The ghostty-applescript community project (github.com/kkilchrist/ghostty-applescript)
    exposes terminal objects with index, title, and working directory.
    """
    script = """\
try
    tell application "Ghostty"
        set output to ""
        set termList to every terminal
        repeat with t in termList
            set tIdx to index of t
            set tTitle to title of t
            set tDir to working directory of t
            set output to output & tIdx & "|||" & tTitle & "|||" & tDir & linefeed
        end repeat
        return output
    end tell
on error
    return ""
end try"""
    output = _osascript(script, timeout=10)
    if not output:
        return []

    windows = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|||")
        if len(parts) < 2:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        title = parts[1] if len(parts) > 1 else ""
        cwd = parts[2] if len(parts) > 2 else None
        windows.append({"index": idx, "title": title, "cwd": cwd})

    return windows


def _get_windows_system_events() -> list[dict]:
    """
    Enumerate Ghostty windows via System Events GUI scripting.

    This is the fallback when the native AppleScript dictionary is not
    available. Only provides window index and title.
    """
    script = """\
tell application "System Events"
    if not (exists process "Ghostty") then return ""
    tell process "Ghostty"
        set output to ""
        set wCount to count of windows
        repeat with i from 1 to wCount
            set wTitle to title of window i
            set output to output & i & "|||" & wTitle & linefeed
        end repeat
        return output
    end tell
end tell"""
    output = _osascript(script, timeout=10)
    if not output:
        return []

    windows = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if "|||" not in line:
            continue
        idx_str, title = line.split("|||", 1)
        try:
            windows.append({
                "index": int(idx_str),
                "title": title,
                "cwd": None,
            })
        except ValueError:
            continue

    return windows


def match_sessions_to_windows(
    sessions: list[TerminalSession], windows: list[dict]
) -> None:
    """
    Match terminal sessions to Ghostty windows.

    If windows have CWD info (from native API), matches by exact CWD.
    Otherwise falls back to heuristic title matching.

    Modifies sessions in-place, setting window_index and window_title.
    """
    if not windows:
        return

    has_cwd = any(w.get("cwd") for w in windows)

    if has_cwd:
        _match_by_cwd(sessions, windows)
    else:
        _match_by_title(sessions, windows)


def _match_by_cwd(
    sessions: list[TerminalSession], windows: list[dict]
) -> None:
    """Match sessions to windows using working directory (exact match)."""
    used_indices: set[int] = set()

    for session in sessions:
        for i, window in enumerate(windows):
            if i in used_indices:
                continue
            win_cwd = window.get("cwd", "")
            if not win_cwd:
                continue
            # Normalize for comparison: both might use ~ or full path
            if _cwds_match(session.cwd, win_cwd):
                session.window_index = window["index"]
                session.window_title = window["title"]
                used_indices.add(i)
                break

    # Assign remaining unmatched by title heuristic
    unmatched = [s for s in sessions if s.window_index is None]
    remaining = [w for i, w in enumerate(windows) if i not in used_indices]
    if unmatched and remaining:
        _match_by_title(unmatched, remaining)


def _cwds_match(cwd1: str, cwd2: str) -> bool:
    """Check if two CWD strings refer to the same directory."""
    home = os.path.expanduser("~")
    # Normalize both to full paths
    p1 = cwd1.replace("~", home, 1) if cwd1.startswith("~") else cwd1
    p2 = cwd2.replace("~", home, 1) if cwd2.startswith("~") else cwd2
    return p1.rstrip("/") == p2.rstrip("/")


def _match_by_title(
    sessions: list[TerminalSession], windows: list[dict]
) -> None:
    """Match sessions to windows using title heuristics."""
    used_indices: set[int] = set()

    for session in sessions:
        best_match: Optional[int] = None
        best_score = 0

        for i, window in enumerate(windows):
            if i in used_indices:
                continue

            title = window.get("title", "")
            score = _match_score(session, title)

            if score > best_score:
                best_score = score
                best_match = i

        if best_match is not None and best_score > 0:
            session.window_index = windows[best_match]["index"]
            session.window_title = windows[best_match]["title"]
            used_indices.add(best_match)

    # For still-unmatched sessions, assign remaining windows in order
    unmatched_sessions = [s for s in sessions if s.window_index is None]
    unmatched_windows = [
        w for i, w in enumerate(windows) if i not in used_indices
    ]
    for session, window in zip(unmatched_sessions, unmatched_windows):
        session.window_index = window["index"]
        session.window_title = window["title"]


def _match_score(session: TerminalSession, title: str) -> int:
    """
    Score how well a session matches a window title.
    Higher score = better match.
    """
    score = 0
    title_lower = title.lower()

    # Full CWD path appears in title (strongest signal)
    if session.cwd and session.cwd != "?" and session.cwd in title:
        score += 10

    # CWD basename appears in title
    if session.cwd and session.cwd != "?":
        basename = os.path.basename(session.cwd.rstrip("/"))
        if basename and basename.lower() in title_lower:
            score += 5

    # Foreground command appears in title
    if session.foreground_cmd and session.foreground_cmd != "?":
        if session.foreground_cmd.lower() in title_lower:
            score += 3

    # Shell name appears in title (weak signal)
    if session.shell and session.shell.lower() in title_lower:
        score += 1

    return score


def raise_ghostty_window(window_index: Optional[int] = None) -> bool:
    """
    Raise a specific Ghostty window to the front.

    Tries Ghostty's native 'focus terminal' first, then falls back
    to AXRaise via System Events.

    Args:
        window_index: 1-based terminal/window index.
            If None, just activates the Ghostty application.

    Returns:
        True if the operation succeeded.
    """
    if window_index is not None:
        # Strategy 1: Try native Ghostty focus command
        native_script = f"""\
try
    tell application "Ghostty" to focus terminal {window_index}
    return "ok"
on error
    return "fallback"
end try"""
        result = _osascript(native_script, timeout=5)

        if result != "ok":
            # Strategy 2: Fall back to AXRaise via System Events
            fallback_script = f"""\
tell application "System Events"
    tell process "Ghostty"
        perform action "AXRaise" of window {window_index}
        set frontmost to true
    end tell
end tell
tell application "Ghostty" to activate"""
            _osascript(fallback_script, timeout=5)
    else:
        _osascript('tell application "Ghostty" to activate', timeout=5)

    return True
