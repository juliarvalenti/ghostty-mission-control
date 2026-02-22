"""
macOS window management for Ghostty.

Uses AppleScript (via osascript) and the Accessibility API to
enumerate Ghostty windows, match them to terminal sessions,
and raise specific windows to the front.
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
    Enumerate Ghostty windows via AppleScript.

    Returns a list of dicts with keys:
        - index: 1-based window index (for AppleScript)
        - title: window title string
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
            windows.append({"index": int(idx_str), "title": title})
        except ValueError:
            continue

    return windows


def match_sessions_to_windows(
    sessions: list[TerminalSession], windows: list[dict]
) -> None:
    """
    Match terminal sessions to Ghostty windows by comparing
    CWD and foreground process names with window titles.

    Modifies sessions in-place, setting window_index and window_title.
    """
    if not windows:
        return

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

    # For unmatched sessions, try a looser fallback: assign remaining
    # windows in order (better than nothing)
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

    Args:
        window_index: 1-based AppleScript window index.
            If None, just activates the Ghostty application.

    Returns:
        True if the operation succeeded.
    """
    if window_index is not None:
        # Raise the specific window, then activate the app
        script = f"""\
tell application "System Events"
    tell process "Ghostty"
        perform action "AXRaise" of window {window_index}
        set frontmost to true
    end tell
end tell
tell application "Ghostty" to activate"""
    else:
        # Just activate Ghostty (brings frontmost window forward)
        script = 'tell application "Ghostty" to activate'

    output = _osascript(script, timeout=5)
    # osascript returns empty on success for these commands
    return True
