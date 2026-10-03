"""Where things live on each OS. The only module that is allowed to know about Linux vs Windows paths.

Linux (KDE Plasma)                      Windows
Hermes home   ~/.hermes                 %LOCALAPPDATA%\\hermes            (Hermes's own defaults; HERMES_HOME overrides)
config        ~/.config/mikronous       %LOCALAPPDATA%\\mikronous
data          ~/.local/share/mikronous  %LOCALAPPDATA%\\mikronous\\data
IPC           $XDG_RUNTIME_DIR/<name>   \\\\.\\pipe\\<name>             (Qt QLocalServer names)
model server  systemd user unit         detached llama-server process with a pid file (mikronous_model.runner)
gateway       hermes-gateway*.service   Hermes_Gateway* scheduled task (installed by `hermes gateway install`)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

INBOX_NAME = "mikronous-inbox"      # plugin -> tray (reminders)
CONTROL_NAME = "mikronous-tray"     # mik toggle/show/quit -> tray


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    return _local_appdata() / "hermes" if IS_WINDOWS else Path("~/.hermes").expanduser()


def conf_dir() -> Path:
    return _local_appdata() / "mikronous" if IS_WINDOWS else Path("~/.config/mikronous").expanduser()


def data_dir() -> Path:
    env = os.environ.get("MIKRONOUS_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return conf_dir() / "data" if IS_WINDOWS else Path("~/.local/share/mikronous").expanduser()


def runtime_dir() -> Path:
    if IS_WINDOWS:
        return conf_dir()
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def local_server_name(name: str) -> str:
    """The name to give QLocalServer.listen(): a full socket path on Linux, a bare pipe name on Windows."""
    if IS_WINDOWS:
        return name
    # Linux: the tray's inbox keeps its historical path so an older plugin still finds it.
    return str(runtime_dir() / ("mikronous.sock" if name == INBOX_NAME else name))


def local_server_path(name: str) -> str:
    """Where a client opens that server: the socket path, or the Windows named-pipe path."""
    if IS_WINDOWS:
        return rf"\\.\pipe\{name}"
    return local_server_name(name)


def pythonw() -> str:
    """Console-less interpreter for background launches on Windows (falls back to sys.executable)."""
    if IS_WINDOWS:
        cand = Path(sys.executable).with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return sys.executable


def hermes_bin() -> str:
    """The hermes launcher, also before the user's PATH picked it up."""
    import shutil
    found = shutil.which("hermes")
    if found:
        return found
    if IS_WINDOWS:
        for name in ("hermes.exe", "hermes.cmd", "hermes.bat", "hermes"):
            cand = hermes_home() / "bin" / name
            if cand.exists():
                return str(cand)
        return "hermes"
    cand = Path("~/.local/bin/hermes").expanduser()
    return str(cand) if cand.exists() else "hermes"
