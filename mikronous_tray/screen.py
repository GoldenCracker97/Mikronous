"""Screen capture for "what's on my screen?": a region via Spectacle on KDE, else the whole primary screen via Qt.

PNGs go under the data dir (``screens/``), the last 20 are kept. Nothing is uploaded: the picture is
base64-encoded into the request to the local gateway, which hands it to the local vision model.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from mikronous_cli.platform import data_dir

KEEP = 20


def screens_dir() -> Path:
    d = data_dir() / "screens"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_path() -> Path:
    return screens_dir() / time.strftime("screen-%Y%m%d-%H%M%S.png")


def prune(keep: int = KEEP) -> None:
    files = sorted(screens_dir().glob("screen-*.png"))
    for f in files[:-keep] if len(files) > keep else []:
        try:
            f.unlink()
        except OSError:
            pass


def spectacle_available() -> bool:
    return sys.platform.startswith("linux") and shutil.which("spectacle") is not None


def capture(region: bool = True) -> Path | None:
    """Returns the PNG path, or None when the user cancelled / nothing could be captured."""
    out = new_path()
    if spectacle_available():
        mode = "-r" if region else "-f"
        try:
            subprocess.run(["spectacle", "-b", "-n", mode, "-o", str(out)], capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            pass
        if out.exists() and out.stat().st_size > 0:
            prune()
            return out
        if region:
            return None                         # the user pressed Esc in the region picker
    return _qt_grab(out)


def _qt_grab(out: Path) -> Path | None:
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    pm = screen.grabWindow(0)
    if pm.isNull() or not pm.save(str(out), "PNG"):
        return None
    prune()
    return out
