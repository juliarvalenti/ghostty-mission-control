# ghostty-mission-control

TUI dashboard for managing multiple Ghostty terminal windows on macOS.

## Features

- Lists all Ghostty terminal sessions with their current working directories
- Shows the foreground process running in each terminal
- Detects Claude Code sessions and displays session IDs
- Arrow key (and vim j/k) navigation
- Press Enter to raise a specific Ghostty window to the front
- Auto-refreshes every 3 seconds

## Usage

```bash
# Run directly
python -m ghostty_mc

# Or install and run
pip install -e .
ghostty-mc
```

## Keybindings

| Key | Action |
|-----|--------|
| `↑` / `k` | Move selection up |
| `↓` / `j` | Move selection down |
| `g` / `G` | Jump to top / bottom |
| `Enter` | Focus the selected Ghostty window |
| `r` | Force refresh |
| `q` / `Esc` | Quit |

## Requirements

- macOS (uses AppleScript for window management)
- Python 3.10+
- No external dependencies (stdlib only)
