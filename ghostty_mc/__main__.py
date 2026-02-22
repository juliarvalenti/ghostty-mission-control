"""Ghostty Mission Control - entry point."""

import curses
import sys


def main() -> None:
    if sys.platform != "darwin":
        print("Ghostty Mission Control requires macOS.")
        print("(Ghostty window management uses macOS-specific APIs)")
        sys.exit(1)

    from ghostty_mc.tui import MissionControl

    try:
        curses.wrapper(MissionControl().run)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
