"""Plugin-side copy of the per-OS data directory rule (the plugin runs inside Hermes, without mikronous_cli)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def conf_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "mikronous"
    return Path("~/.config/mikronous").expanduser()


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "hermes"
    return Path("~/.hermes").expanduser()


def data_dir() -> Path:
    env = os.environ.get("MIKRONOUS_DATA_DIR")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "mikronous" / "data"
    return Path("~/.local/share/mikronous").expanduser()
