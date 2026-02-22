"""
Curses-based TUI for Ghostty Mission Control.

Displays a navigable list of Ghostty terminal sessions with CWD,
foreground process, and Claude Code session information.
"""

import curses
import time
from typing import Optional

from ghostty_mc.discovery import TerminalSession, discover_sessions
from ghostty_mc.window import (
    get_ghostty_windows,
    match_sessions_to_windows,
    raise_ghostty_window,
)

# Color pair IDs
C_HEADER = 1
C_SELECTED = 2
C_CLAUDE = 3
C_STATUS_BAR = 4
C_DIM = 5
C_BORDER = 6
C_TITLE = 7

REFRESH_INTERVAL = 3  # seconds


class MissionControl:
    """Main TUI application class."""

    def __init__(self) -> None:
        self.sessions: list[TerminalSession] = []
        self.selected: int = 0
        self.last_refresh: float = 0
        self.message: str = ""
        self.message_time: float = 0
        self.has_colors: bool = False

    def run(self, stdscr: "curses.window") -> None:
        """Main TUI event loop."""
        self.stdscr = stdscr
        curses.curs_set(0)

        # Set up colors
        if curses.has_colors():
            curses.use_default_colors()
            self.has_colors = True
            curses.init_pair(C_HEADER, curses.COLOR_GREEN, -1)
            curses.init_pair(C_SELECTED, curses.COLOR_BLACK, curses.COLOR_WHITE)
            curses.init_pair(C_CLAUDE, curses.COLOR_MAGENTA, -1)
            curses.init_pair(C_STATUS_BAR, curses.COLOR_BLACK, curses.COLOR_GREEN)
            curses.init_pair(C_DIM, curses.COLOR_WHITE, -1)
            curses.init_pair(C_BORDER, curses.COLOR_GREEN, -1)
            curses.init_pair(C_TITLE, curses.COLOR_GREEN, -1)

        # Non-blocking input with 500ms timeout
        stdscr.timeout(500)

        self._refresh_sessions()

        while True:
            self._draw()
            key = stdscr.getch()

            if key == ord("q") or key == ord("Q") or key == 27:  # q/Q/Esc
                break
            elif key == curses.KEY_UP or key == ord("k"):
                if self.sessions:
                    self.selected = (self.selected - 1) % len(self.sessions)
            elif key == curses.KEY_DOWN or key == ord("j"):
                if self.sessions:
                    self.selected = (self.selected + 1) % len(self.sessions)
            elif key == ord("g"):
                self.selected = 0
            elif key == ord("G"):
                if self.sessions:
                    self.selected = len(self.sessions) - 1
            elif key in (ord("\n"), curses.KEY_ENTER, 10, 13):
                self._activate_selected()
            elif key == ord("r") or key == ord("R"):
                self._refresh_sessions()
                self.message = "Refreshed"
                self.message_time = time.time()
            elif key == curses.KEY_RESIZE:
                stdscr.clear()

            # Auto-refresh
            if time.time() - self.last_refresh > REFRESH_INTERVAL:
                self._refresh_sessions()

    def _refresh_sessions(self) -> None:
        """Refresh the session list from process data."""
        self.sessions = discover_sessions()

        # Match sessions to macOS windows
        windows = get_ghostty_windows()
        if windows:
            match_sessions_to_windows(self.sessions, windows)

        # Keep selection in bounds
        if self.sessions:
            self.selected = min(self.selected, len(self.sessions) - 1)
        else:
            self.selected = 0

        self.last_refresh = time.time()

    def _activate_selected(self) -> None:
        """Raise the window for the selected session."""
        if not self.sessions:
            return

        session = self.sessions[self.selected]
        if session.window_index is not None:
            raise_ghostty_window(session.window_index)
            self.message = f"Focused: {session.cwd}"
        else:
            raise_ghostty_window(None)
            self.message = "Activated Ghostty"

        self.message_time = time.time()

    def _draw(self) -> None:
        """Draw the entire TUI."""
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()

        if height < 8 or width < 40:
            self._draw_too_small(height, width)
            return

        row = 0

        # Title bar
        row = self._draw_title(row, width)

        # Column headers
        row = self._draw_headers(row, width)

        # Session rows
        row = self._draw_sessions(row, height, width)

        # Status bar at the bottom
        self._draw_status_bar(height, width)

        self.stdscr.refresh()

    def _draw_too_small(self, height: int, width: int) -> None:
        """Show message when terminal is too small."""
        msg = "Terminal too small"
        try:
            self.stdscr.addstr(0, 0, msg[:width])
        except curses.error:
            pass
        self.stdscr.refresh()

    def _draw_title(self, row: int, width: int) -> int:
        """Draw the title bar. Returns next row."""
        title = " GHOSTTY MISSION CONTROL "
        # Center the title in a decorative line
        padding = max(0, width - len(title)) // 2
        line = "─" * padding + title + "─" * (width - padding - len(title))

        attr = curses.color_pair(C_TITLE) | curses.A_BOLD if self.has_colors else curses.A_BOLD
        self._addstr(row, 0, line[:width], attr)

        row += 1
        self._addstr(row, 0, " " * width, 0)
        return row + 1

    def _draw_headers(self, row: int, width: int) -> int:
        """Draw column headers. Returns next row."""
        cols = self._column_widths(width)
        attr = curses.color_pair(C_HEADER) | curses.A_BOLD if self.has_colors else curses.A_BOLD

        header = self._format_row(
            " ",
            "CWD",
            "PID",
            "Process",
            "Claude Code",
            cols,
        )
        self._addstr(row, 0, header[:width], attr)
        row += 1

        # Separator line
        sep_attr = curses.color_pair(C_DIM) if self.has_colors else curses.A_DIM
        self._addstr(row, 0, "─" * min(width, sum(cols.values()) + 10), sep_attr)
        return row + 1

    def _draw_sessions(self, row: int, height: int, width: int) -> int:
        """Draw the session list. Returns next row."""
        # Reserve 2 rows for status bar
        max_row = height - 2

        if not self.sessions:
            msg = "  No Ghostty sessions found. Is Ghostty running?"
            attr = curses.color_pair(C_DIM) if self.has_colors else curses.A_DIM
            self._addstr(row, 0, msg[:width], attr)
            row += 2
            hint = "  Press 'r' to refresh, 'q' to quit"
            self._addstr(row, 0, hint[:width], attr)
            return row + 1

        cols = self._column_widths(width)

        # Calculate scroll offset if list is too long
        visible_rows = max_row - row
        scroll_offset = 0
        if len(self.sessions) > visible_rows:
            # Keep selected item visible with some context
            if self.selected >= scroll_offset + visible_rows - 1:
                scroll_offset = self.selected - visible_rows + 2
            if self.selected < scroll_offset + 1:
                scroll_offset = max(0, self.selected - 1)

        for i, session in enumerate(self.sessions):
            if i < scroll_offset:
                continue
            if row >= max_row:
                # Show "more" indicator
                more = f"  ... {len(self.sessions) - i} more"
                attr = curses.color_pair(C_DIM) if self.has_colors else curses.A_DIM
                self._addstr(row, 0, more[:width], attr)
                row += 1
                break

            is_selected = i == self.selected
            marker = "▸" if is_selected else " "

            # Claude Code display
            claude_str = ""
            if session.claude_running:
                if session.claude_session_id:
                    claude_str = session.claude_session_id
                else:
                    claude_str = "● active"

            line = self._format_row(
                marker,
                session.cwd,
                str(session.shell_pid),
                session.foreground_cmd,
                claude_str,
                cols,
            )

            if is_selected:
                attr = curses.color_pair(C_SELECTED) | curses.A_BOLD if self.has_colors else curses.A_REVERSE
                # Pad to full width for highlight bar
                line = line.ljust(width)
            elif session.claude_running:
                attr = curses.color_pair(C_CLAUDE) if self.has_colors else 0
            else:
                attr = 0

            self._addstr(row, 0, line[:width], attr)
            row += 1

        return row

    def _draw_status_bar(self, height: int, width: int) -> None:
        """Draw the status bar at the bottom of the screen."""
        bar_row = height - 1

        # Status message or help text
        if self.message and time.time() - self.message_time < 3:
            status = f" {self.message}"
        else:
            count = len(self.sessions)
            status = f" {count} session{'s' if count != 1 else ''}"

        help_text = " ↑↓/jk Navigate  Enter Focus  r Refresh  q Quit "

        # Right-align help text
        gap = width - len(status) - len(help_text)
        if gap < 0:
            # Not enough space, truncate
            bar = (status + help_text)[:width]
        else:
            bar = status + " " * gap + help_text

        attr = curses.color_pair(C_STATUS_BAR) if self.has_colors else curses.A_REVERSE
        self._addstr(bar_row, 0, bar.ljust(width)[:width], attr)

    def _column_widths(self, total_width: int) -> dict[str, int]:
        """Calculate column widths based on available terminal width."""
        # Fixed columns
        marker_w = 2
        pid_w = 8
        process_w = 12

        # Variable columns split remaining space
        remaining = total_width - marker_w - pid_w - process_w - 6  # 6 for separators
        cwd_w = max(15, int(remaining * 0.5))
        claude_w = max(10, remaining - cwd_w)

        return {
            "marker": marker_w,
            "cwd": cwd_w,
            "pid": pid_w,
            "process": process_w,
            "claude": claude_w,
        }

    def _format_row(
        self,
        marker: str,
        cwd: str,
        pid: str,
        process: str,
        claude: str,
        cols: dict[str, int],
    ) -> str:
        """Format a single row with proper column alignment."""
        marker_s = f"{marker:<{cols['marker']}}"
        cwd_s = self._truncate(cwd, cols["cwd"])
        pid_s = f"{pid:<{cols['pid']}}"
        proc_s = f"{process:<{cols['process']}}"
        claude_s = self._truncate(claude, cols["claude"])

        return f"{marker_s}{cwd_s}  {pid_s} {proc_s} {claude_s}"

    @staticmethod
    def _truncate(text: str, width: int) -> str:
        """Truncate text to width, adding ellipsis if needed."""
        if len(text) <= width:
            return f"{text:<{width}}"
        return text[: width - 1] + "…"

    def _addstr(self, row: int, col: int, text: str, attr: int = 0) -> None:
        """Safe wrapper around addstr that ignores curses errors."""
        try:
            self.stdscr.addstr(row, col, text, attr)
        except curses.error:
            pass
