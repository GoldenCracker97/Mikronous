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
MAX_EDGE = 1536          # long edge after downscaling: legible text, a fraction of the vision tokens and encoder memory


def screens_dir() -> Path:
    d = data_dir() / "screens"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_path() -> Path:
    ms = int((time.time() % 1) * 1000)
    return screens_dir() / (time.strftime("screen-%Y%m%d-%H%M%S") + f"-{ms:03d}.png")


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
            downscale(out)
            prune()
            return out
        if region:
            return None                         # the user pressed Esc in the region picker
    return _qt_grab(out)


def downscale(path: Path, max_edge: int = MAX_EDGE) -> tuple[int, int]:
    """Shrink a PNG in place so its long edge is at most ``max_edge``; returns the final size."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    img = QImage(str(path))
    if img.isNull():
        return (0, 0)
    if max(img.width(), img.height()) > max_edge:
        img = img.scaled(max_edge, max_edge, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img.save(str(path), "PNG")
    return (img.width(), img.height())


def start_capture(parent, region: bool, on_done) -> None:
    """Non-blocking capture: Spectacle as a QProcess (the tray keeps serving reminders, KRunner and CEASE while
    the user draws the region); ``on_done(path | None)`` runs on the Qt thread. Falls back to :func:`capture`."""
    if not spectacle_available():
        on_done(capture(region))
        return
    from PySide6.QtCore import QProcess
    out = new_path()
    proc = QProcess(parent)

    def finished(_code, _status):
        proc.deleteLater()
        if out.exists() and out.stat().st_size > 0:
            downscale(out)
            prune()
            on_done(out)
        elif region:
            on_done(None)                        # Esc in the region picker
        else:
            on_done(_qt_grab(out))
    proc.finished.connect(finished)
    proc.start("spectacle", ["-b", "-n", "-r" if region else "-f", "-o", str(out)])
    if not proc.waitForStarted(3000):
        proc.deleteLater()
        on_done(_qt_grab(out))


def _qt_grab(out: Path) -> Path | None:
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    pm = screen.grabWindow(0)
    if pm.isNull() or not pm.save(str(out), "PNG"):
        return None
    downscale(out)
    prune()
    return out
