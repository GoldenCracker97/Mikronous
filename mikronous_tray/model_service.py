"""Load and unload the local model with the tray.

The model lives in ``mikronous-llama.service`` (llama-server, systemd user unit). The tray starts it
when it launches and stops it on Quit, so quitting Mikronous frees the VRAM. ``MIKRONOUS_KEEP_MODEL=1``
keeps the service running across Quit. Everything is best-effort and silent where systemd is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request

from PySide6.QtCore import QObject, QTimer, Signal

UNIT = "mikronous-llama.service"
POLL_MS = 1500
WAKE_TIMEOUT_MS = 180_000     # a 14B model on a slow disk can take a while


def _systemctl(*args: str) -> subprocess.CompletedProcess | None:
    if not shutil.which("systemctl"):
        return None
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _models_url() -> str:
    try:
        from mikronous_cli.paths import LLAMA_ENV, read_env
        port = read_env(LLAMA_ENV).get("LLAMA_PORT", "8081")
    except Exception:  # noqa: BLE001
        port = "8081"
    return f"http://127.0.0.1:{port}/v1/models"


class ModelService(QObject):
    """States: ``ready`` (answers on its port), ``waking`` (unit started, not answering yet),
    ``unloaded`` (unit inactive), ``unknown`` (no systemd)."""
    state_changed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.state = "unknown"
        self._poll = QTimer(self, interval=POLL_MS)
        self._poll.timeout.connect(self._tick)
        self._waited = 0

    # ------------------------------------------------------------------ queries
    @staticmethod
    def available() -> bool:
        return shutil.which("systemctl") is not None

    @staticmethod
    def unit_active() -> bool:
        r = _systemctl("is-active", UNIT)
        return bool(r) and r.stdout.strip() == "active"

    @staticmethod
    def answers() -> bool:
        try:
            with urllib.request.urlopen(_models_url(), timeout=1.5) as resp:  # noqa: S310 - loopback
                return bool(json.loads(resp.read().decode("utf-8") or "{}").get("data"))
        except Exception:  # noqa: BLE001
            return False

    def refresh(self) -> str:
        if not self.available():
            new = "ready" if self.answers() else "unknown"
        elif self.answers():
            new = "ready"
        elif self.unit_active():
            new = "waking"
        else:
            new = "unloaded"
        self._set(new)
        return new

    # ------------------------------------------------------------------ actions
    def ensure_loaded(self) -> None:
        """Start the unit if needed and poll until the model answers."""
        if self.refresh() == "ready":
            return
        if self.available() and self.state == "unloaded":
            _systemctl("start", UNIT)
        self._set("waking")
        self._waited = 0
        self._poll.start()

    def unload(self) -> None:
        self._poll.stop()
        if self.available():
            _systemctl("stop", UNIT)
        self._set("unloaded")

    def unload_on_quit(self) -> bool:
        return os.environ.get("MIKRONOUS_KEEP_MODEL", "") not in ("1", "true", "yes")

    # ------------------------------------------------------------------ internals
    def _tick(self) -> None:
        self._waited += POLL_MS
        if self.answers():
            self._poll.stop()
            self._set("ready")
        elif self._waited >= WAKE_TIMEOUT_MS or (self.available() and not self.unit_active()):
            self._poll.stop()
            self._set("unloaded" if not self.unit_active() else "waking")

    def _set(self, new: str) -> None:
        if new != self.state:
            self.state = new
            self.state_changed.emit(new)
