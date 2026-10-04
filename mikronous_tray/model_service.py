"""Load and unload the local model with the tray.

The model is llama-server, run by ``mikronous_model.runner`` (systemd unit on Linux, a detached process
on Windows). The tray starts it when it launches and stops it on Quit, so quitting Mikronous frees the
VRAM. ``MIKRONOUS_KEEP_MODEL=1`` keeps it running across Quit. Best-effort where no manager exists.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, QTimer, Signal

from mikronous_model import runner

UNIT = runner.UNIT
POLL_MS = 1500
SLOW_POLL_MS = 10_000
WAKE_TIMEOUT_MS = 180_000     # a 14B model on a slow disk can take a while; after this we poll slowly, never give up


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
        return runner.available()

    @staticmethod
    def unit_active() -> bool:
        return runner.is_running()

    @staticmethod
    def answers() -> bool:
        return runner.answers()

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
            runner.start()
        if runner.IS_WINDOWS:                     # no service manager autostarts the embedding server there
            try:
                from mikronous_cli import embed
                if embed.enabled() and not runner.is_running("embed"):
                    runner.start("embed")
            except Exception:  # noqa: BLE001 - optional feature
                pass
        self._set("waking")
        self._waited = 0
        self._poll.setInterval(POLL_MS)
        self._poll.start()

    def unload(self) -> None:
        self._poll.stop()
        if self.available():
            runner.stop()
        self._set("unloaded")

    def unload_on_quit(self) -> bool:
        if os.environ.get("MIKRONOUS_KEEP_MODEL", "") in ("1", "true", "yes"):
            return False
        from . import settings
        return not settings.load().get("keep_model", False)

    # ------------------------------------------------------------------ internals
    def _tick(self) -> None:
        self._waited += POLL_MS
        if self.answers():
            self._poll.stop()
            self._set("ready")
        elif self.available() and not self.unit_active():
            self._poll.stop()
            self._set("unloaded")
        elif self._waited >= WAKE_TIMEOUT_MS and self._poll.interval() != SLOW_POLL_MS:
            self._poll.setInterval(SLOW_POLL_MS)   # still loading (huge model, slow disk): keep looking, slowly
            self._set("waking")

    def _set(self, new: str) -> None:
        if new != self.state:
            self.state = new
            self.state_changed.emit(new)
