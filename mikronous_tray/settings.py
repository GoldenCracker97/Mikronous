"""Tray-side persistent state: ~/.config/mikronous/tray.json (current chat session id, window size).

The gateway endpoint and API key are NOT stored here: ``mikronous_cli.paths.gateway()`` reads them
from the Hermes profile so there is exactly one source of truth.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mikronous_cli.paths import CONF_DIR
from mikronous_cli.platform import CONTROL_NAME, INBOX_NAME, local_server_name

STATE_FILE = CONF_DIR / "tray.json"
DEFAULTS = {
    "session_id": "",
    "window": {"width": 520, "height": 680},
    "notes_dir": os.environ.get("MIKRONOUS_NOTES_DIR", "~/Mikronous/notes"),
    "hotkey": "Ctrl+Alt+Space",     # Windows only; on KDE the shortcut lives in kglobalshortcutsrc
    "litany": True,                 # boot litany on the first show after start (MIKRONOUS_NO_LITANY=1 overrides)
    "keep_model": False,            # keep llama-server loaded when the tray quits (MIKRONOUS_KEEP_MODEL=1 overrides)
    "sidebar": False,               # past-chats pane open
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return data


def save(**changes) -> None:
    data = load()
    data.update(changes)
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def socket_path() -> Path:
    """QLocalServer name the Hermes plugin pushes to (hermes_plugin/mikronous/deliver.py): a socket path on
    Linux ($XDG_RUNTIME_DIR/mikronous.sock), a pipe name on Windows."""
    return Path(local_server_name(INBOX_NAME))


CONTROL_SERVER = CONTROL_NAME   # QLocalServer name for single-instance + `mik toggle`
