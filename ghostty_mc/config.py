"""
Persistent configuration for Ghostty Mission Control.

Stores user-defined session names keyed by CWD, so terminals
in a given project directory always show their custom name.

Config file: ~/.config/ghostty-mc/config.json
"""

import json
import os
from pathlib import Path

CONFIG_DIR = os.path.join(str(Path.home()), ".config", "ghostty-mc")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def _load_config() -> dict:
    """Load the config file, returning empty dict on failure."""
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_config(config: dict) -> None:
    """Save the config file, creating directories as needed."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
    except OSError:
        pass


def get_session_name(cwd: str) -> str:
    """Get the custom name for a session by its CWD, or empty string."""
    config = _load_config()
    names = config.get("names", {})
    return names.get(cwd, "")


def set_session_name(cwd: str, name: str) -> None:
    """Set a custom name for a session by its CWD."""
    config = _load_config()
    if "names" not in config:
        config["names"] = {}
    if name:
        config["names"][cwd] = name
    else:
        config["names"].pop(cwd, None)
    _save_config(config)


def get_all_names() -> dict[str, str]:
    """Get all custom session names as {cwd: name}."""
    config = _load_config()
    return config.get("names", {})
