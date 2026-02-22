"""
Curses-based TUI for Ghostty Mission Control.

Split-pane layout:
  Top: navigable list of Ghostty terminal sessions
  Bottom: preview pane showing session details (Claude transcript, todos,
          terminal content, or basic process info)

Supports session naming with persistent storage.
"""

import curses
import time
from typing import Optional

from ghostty_mc.config import get_all_names, set_session_name
from ghostty_mc.discovery import TerminalSession, discover_sessions
from ghostty_mc.preview import PreviewData, get_preview
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
C_PREVIEW_HEADER = 8

REFRESH_INTERVAL = 3  # seconds
PREVIEW_REFRESH_INTERVAL = 2  # seconds


class MissionControl:
    """Main TUI application class."""

    def __init__(self) -> None:
        self.sessions: list[TerminalSession] = []
        self.selected: int = 0
        self.last_refresh: float = 0
        self.last_preview_refresh: float = 0
        self.preview: Optional[PreviewData] = None
        self.preview_scroll: int = 0
        self.message: str = ""
        self.message_time: float = 0
        self.has_colors: bool = False
        self.naming_mode: bool = False
        self.name_buffer: str = ""
        self.session_names: dict[str, str] = {}

    def run(self, stdscr: "curses.window") -> None:
        """Main TUI event loop."""
        self.stdscr = stdscr
        curses.curs_set(0)

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
            curses.init_pair(C_PREVIEW_HEADER, curses.COLOR_CYAN, -1)

        stdscr.timeout(400)

        self.session_names = get_all_names()
        self._refresh_sessions()
        self._refresh_preview()

        while True:
            self._draw()
            key = stdscr.getch()

            if self.naming_mode:
                self._handle_naming_input(key)
                continue

            if key == ord("q") or key == ord("Q") or key == 27:
                break
            elif key == curses.KEY_UP or key == ord("k"):
                if self.sessions:
                    self.selected = (self.selected - 1) % len(self.sessions)
                    self.preview_scroll = 0
                    self._refresh_preview()
            elif key == curses.KEY_DOWN or key == ord("j"):
                if self.sessions:
                    self.selected = (self.selected + 1) % len(self.sessions)
                    self.preview_scroll = 0
                    self._refresh_preview()
            elif key == ord("g"):
                self.selected = 0
                self.preview_scroll = 0
                self._refresh_preview()
            elif key == ord("G"):
                if self.sessions:
                    self.selected = len(self.sessions) - 1
                    self.preview_scroll = 0
                    self._refresh_preview()
            elif key in (ord("\n"), curses.KEY_ENTER, 10, 13):
                self._activate_selected()
            elif key == ord("r") or key == ord("R"):
                self._refresh_sessions()
                self._refresh_preview()
                self.message = "Refreshed"
                self.message_time = time.time()
            elif key == ord("n") or key == ord("N"):
                self._start_naming()
            elif key == curses.KEY_NPAGE or key == ord("J"):
                # Page down in preview
                self.preview_scroll += 5
            elif key == curses.KEY_PPAGE or key == ord("K"):
                # Page up in preview
                self.preview_scroll = max(0, self.preview_scroll - 5)
            elif key == curses.KEY_RESIZE:
                stdscr.clear()

            # Auto-refresh sessions
            now = time.time()
            if now - self.last_refresh > REFRESH_INTERVAL:
                self._refresh_sessions()
            if now - self.last_preview_refresh > PREVIEW_REFRESH_INTERVAL:
                self._refresh_preview()

    def _refresh_sessions(self) -> None:
        """Refresh the session list from process data."""
        self.sessions = discover_sessions()

        windows = get_ghostty_windows()
        if windows:
            match_sessions_to_windows(self.sessions, windows)

        # Apply custom names
        for session in self.sessions:
            name = self.session_names.get(session.cwd, "")
            if name:
                session.custom_name = name

        if self.sessions:
            self.selected = min(self.selected, len(self.sessions) - 1)
        else:
            self.selected = 0

        self.last_refresh = time.time()

    def _refresh_preview(self) -> None:
        """Refresh preview data for the selected session."""
        if self.sessions and 0 <= self.selected < len(self.sessions):
            self.preview = get_preview(self.sessions[self.selected])
        else:
            self.preview = None
        self.last_preview_refresh = time.time()

    def _activate_selected(self) -> None:
        """Raise the window for the selected session."""
        if not self.sessions:
            return

        session = self.sessions[self.selected]
        if session.window_index is not None:
            raise_ghostty_window(session.window_index)
            label = session.custom_name or session.cwd
            self.message = f"Focused: {label}"
        else:
            raise_ghostty_window(None)
            self.message = "Activated Ghostty"
        self.message_time = time.time()

    def _start_naming(self) -> None:
        """Enter naming mode for the selected session."""
        if not self.sessions:
            return
        session = self.sessions[self.selected]
        self.naming_mode = True
        self.name_buffer = session.custom_name
        curses.curs_set(1)

    def _handle_naming_input(self, key: int) -> None:
        """Handle keyboard input during naming mode."""
        if key in (27,):  # Escape — cancel
            self.naming_mode = False
            self.name_buffer = ""
            curses.curs_set(0)
        elif key in (ord("\n"), curses.KEY_ENTER, 10, 13):  # Enter — save
            if self.sessions:
                session = self.sessions[self.selected]
                session.custom_name = self.name_buffer
                set_session_name(session.cwd, self.name_buffer)
                self.session_names = get_all_names()
                self.message = f"Named: {self.name_buffer}" if self.name_buffer else "Name cleared"
                self.message_time = time.time()
            self.naming_mode = False
            self.name_buffer = ""
            curses.curs_set(0)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.name_buffer = self.name_buffer[:-1]
        elif 32 <= key < 127:
            self.name_buffer += chr(key)

    # ── Drawing ──────────────────────────────────────────────────────

    def _draw(self) -> None:
        """Draw the entire split-pane TUI."""
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()

        if height < 10 or width < 40:
            self._draw_too_small(height, width)
            return

        # Layout: title(2) + headers(2) + sessions(variable) + divider(1) + preview(variable) + status(1)
        # Split: top ~40% for sessions, bottom ~60% for preview
        status_rows = 1
        title_rows = 2
        header_rows = 2
        divider_rows = 1
        usable = height - status_rows - title_rows - header_rows - divider_rows

        # At least 3 rows for sessions, rest for preview
        session_rows = max(3, min(len(self.sessions) + 1, usable * 2 // 5))
        preview_rows = usable - session_rows

        row = 0

        # Title
        row = self._draw_title(row, width)

        # Column headers
        row = self._draw_headers(row, width)

        # Session rows
        session_end = row + session_rows
        row = self._draw_sessions(row, session_end, width)

        # Fill remaining session area
        while row < session_end:
            self._addstr(row, 0, " " * width, 0)
            row += 1

        # Divider
        row = self._draw_divider(row, width)

        # Preview pane
        preview_end = height - status_rows
        self._draw_preview(row, preview_end, width)

        # Status bar
        self._draw_status_bar(height, width)

        self.stdscr.refresh()

    def _draw_too_small(self, height: int, width: int) -> None:
        msg = "Terminal too small"
        try:
            self.stdscr.addstr(0, 0, msg[:width])
        except curses.error:
            pass
        self.stdscr.refresh()

    def _draw_title(self, row: int, width: int) -> int:
        title = " GHOSTTY MISSION CONTROL "
        padding = max(0, width - len(title)) // 2
        line = "─" * padding + title + "─" * (width - padding - len(title))
        attr = curses.color_pair(C_TITLE) | curses.A_BOLD if self.has_colors else curses.A_BOLD
        self._addstr(row, 0, line[:width], attr)
        row += 1
        self._addstr(row, 0, " " * width, 0)
        return row + 1

    def _draw_headers(self, row: int, width: int) -> int:
        cols = self._column_widths(width)
        attr = curses.color_pair(C_HEADER) | curses.A_BOLD if self.has_colors else curses.A_BOLD

        header = self._format_row(" ", "CWD", "PID", "Process", "Claude Code", cols)
        self._addstr(row, 0, header[:width], attr)
        row += 1

        sep_attr = curses.color_pair(C_DIM) if self.has_colors else curses.A_DIM
        self._addstr(row, 0, "─" * width, sep_attr)
        return row + 1

    def _draw_sessions(self, row: int, max_row: int, width: int) -> int:
        if not self.sessions:
            msg = "  No Ghostty sessions found. Is Ghostty running?"
            attr = curses.color_pair(C_DIM) if self.has_colors else curses.A_DIM
            self._addstr(row, 0, msg[:width], attr)
            return row + 1

        cols = self._column_widths(width)

        # Scroll to keep selected visible
        visible = max_row - row
        scroll_offset = 0
        if len(self.sessions) > visible:
            if self.selected >= visible - 1:
                scroll_offset = self.selected - visible + 2
            scroll_offset = max(0, min(scroll_offset, len(self.sessions) - visible))

        for i, session in enumerate(self.sessions):
            if i < scroll_offset:
                continue
            if row >= max_row:
                break

            is_selected = i == self.selected
            marker = "▸" if is_selected else " "

            # Display name: custom name takes precedence, show CWD basename after
            if session.custom_name:
                cwd_display = f"{session.custom_name}  {session.cwd}"
            else:
                cwd_display = session.cwd

            claude_str = ""
            if session.claude_running:
                if session.claude_session_id:
                    claude_str = session.claude_session_id
                else:
                    claude_str = "● active"

            line = self._format_row(
                marker, cwd_display, str(session.shell_pid),
                session.foreground_cmd, claude_str, cols,
            )

            if is_selected:
                attr = curses.color_pair(C_SELECTED) | curses.A_BOLD if self.has_colors else curses.A_REVERSE
                line = line.ljust(width)
            elif session.claude_running:
                attr = curses.color_pair(C_CLAUDE) if self.has_colors else 0
            else:
                attr = 0

            self._addstr(row, 0, line[:width], attr)
            row += 1

        return row

    def _draw_divider(self, row: int, width: int) -> int:
        label = " PREVIEW "
        padding = max(0, width - len(label)) // 2
        line = "─" * padding + label + "─" * (width - padding - len(label))
        attr = curses.color_pair(C_PREVIEW_HEADER) | curses.A_BOLD if self.has_colors else curses.A_BOLD
        self._addstr(row, 0, line[:width], attr)
        return row + 1

    def _draw_preview(self, row: int, max_row: int, width: int) -> None:
        if not self.preview or not self.preview.lines:
            attr = curses.color_pair(C_DIM) if self.has_colors else curses.A_DIM
            self._addstr(row, 0, "  (no preview available)", attr)
            return

        lines = self.preview.lines

        # Clamp scroll
        visible = max_row - row
        max_scroll = max(0, len(lines) - visible)
        self.preview_scroll = min(self.preview_scroll, max_scroll)

        start = self.preview_scroll
        for i, line in enumerate(lines[start:]):
            if row >= max_row:
                break

            # First line of preview gets special formatting
            if i == 0 and start == 0:
                attr = curses.color_pair(C_PREVIEW_HEADER) if self.has_colors else curses.A_BOLD
            elif line.startswith("Tasks:") or line.startswith("Last prompt:") or line.startswith("Last response:"):
                attr = curses.color_pair(C_HEADER) if self.has_colors else curses.A_BOLD
            elif line.startswith("  [x]"):
                attr = curses.color_pair(C_DIM) if self.has_colors else curses.A_DIM
            elif line.startswith("  [>]"):
                attr = curses.color_pair(C_CLAUDE) if self.has_colors else curses.A_BOLD
            else:
                attr = 0

            display = f"  {line}"
            self._addstr(row, 0, display[:width], attr)
            row += 1

        # Scroll indicator
        if max_scroll > 0 and row < max_row:
            indicator = f"  [{self.preview_scroll + 1}-{min(self.preview_scroll + visible, len(lines))}/{len(lines)}]"
            attr = curses.color_pair(C_DIM) if self.has_colors else curses.A_DIM
            self._addstr(row, 0, indicator[:width], attr)

    def _draw_status_bar(self, height: int, width: int) -> None:
        bar_row = height - 1

        if self.naming_mode:
            prompt = f" Name: {self.name_buffer}█"
            hint = " Enter Save  Esc Cancel "
            gap = width - len(prompt) - len(hint)
            bar = prompt + " " * max(0, gap) + hint
        elif self.message and time.time() - self.message_time < 3:
            status = f" {self.message}"
            help_text = " ↑↓ Navigate  Enter Focus  n Name  q Quit "
            gap = width - len(status) - len(help_text)
            bar = status + " " * max(0, gap) + help_text
        else:
            count = len(self.sessions)
            status = f" {count} session{'s' if count != 1 else ''}"
            help_text = " ↑↓/jk Nav  Enter Focus  n Name  J/K Scroll  r Refresh  q Quit "
            gap = width - len(status) - len(help_text)
            bar = status + " " * max(0, gap) + help_text

        attr = curses.color_pair(C_STATUS_BAR) if self.has_colors else curses.A_REVERSE
        self._addstr(bar_row, 0, bar.ljust(width)[:width], attr)

    # ── Helpers ──────────────────────────────────────────────────────

    def _column_widths(self, total_width: int) -> dict[str, int]:
        marker_w = 2
        pid_w = 8
        process_w = 12
        remaining = total_width - marker_w - pid_w - process_w - 6
        cwd_w = max(20, int(remaining * 0.55))
        claude_w = max(10, remaining - cwd_w)
        return {
            "marker": marker_w,
            "cwd": cwd_w,
            "pid": pid_w,
            "process": process_w,
            "claude": claude_w,
        }

    def _format_row(
        self, marker: str, cwd: str, pid: str, process: str,
        claude: str, cols: dict[str, int],
    ) -> str:
        marker_s = f"{marker:<{cols['marker']}}"
        cwd_s = self._truncate(cwd, cols["cwd"])
        pid_s = f"{pid:<{cols['pid']}}"
        proc_s = f"{process:<{cols['process']}}"
        claude_s = self._truncate(claude, cols["claude"])
        return f"{marker_s}{cwd_s}  {pid_s} {proc_s} {claude_s}"

    @staticmethod
    def _truncate(text: str, width: int) -> str:
        if len(text) <= width:
            return f"{text:<{width}}"
        return text[: width - 1] + "…"

    def _addstr(self, row: int, col: int, text: str, attr: int = 0) -> None:
        try:
            self.stdscr.addstr(row, col, text, attr)
        except curses.error:
            pass
