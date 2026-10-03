"""Tray icon, menu, single-instance control socket, and wiring between window, inbox and gateway."""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import settings
from .chat_window import ChatWindow
from .hermes_client import HermesClient
from .inbox import InboxServer
from .model_service import ModelService


def _tray_icon() -> QIcon:
    """Monochrome cog for the panel: light glyph on a dark panel, dark glyph on a light one."""
    from . import theme
    pal = QApplication.palette()
    dark_panel = pal.window().color().lightness() < 128
    return theme.qicon("symbolic-light" if dark_panel else "symbolic-dark")


def send_control(command: str) -> bool:
    """Talk to a running tray instance. Returns False when none is running."""
    sock = QLocalSocket()
    sock.connectToServer(settings.CONTROL_SERVER)
    if not sock.waitForConnected(500):
        return False
    sock.write((command + "\n").encode())
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


class TrayApp(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName("Mikronous")
        app.setDesktopFileName("mikronous")
        self.client = HermesClient()
        self.window = ChatWindow(self.client)
        from . import theme
        app.setWindowIcon(theme.qicon("color"))
        self.window.setWindowIcon(theme.qicon("color"))
        self._litany_pending = True

        self.control = QLocalServer(self)
        QLocalServer.removeServer(settings.CONTROL_SERVER)
        self.control.listen(settings.CONTROL_SERVER)
        self.control.newConnection.connect(self._control_conn)

        self.inbox = InboxServer(self)
        if not self.inbox.start():
            print("mikronous-tray: could not listen on", settings.socket_path(), file=sys.stderr)
        self.inbox.message.connect(self._inbox_message)

        self.tray = QSystemTrayIcon(_tray_icon(), self)
        self.tray.setToolTip("Mikronous")
        menu = QMenu()
        self.act_toggle = QAction("Show / hide chat", menu)
        self.act_toggle.triggered.connect(self.window.toggle)
        menu.addAction(self.act_toggle)
        a = QAction("New chat", menu)
        a.triggered.connect(lambda: (self.window.new_chat(), self.window.show_window()))
        menu.addAction(a)
        a = QAction("Open notes folder", menu)
        a.triggered.connect(self._open_notes)
        menu.addAction(a)
        a = QAction("Gateway status", menu)
        a.triggered.connect(self._status)
        menu.addAction(a)
        menu.addSeparator()
        self.act_model = QAction("Unload model (free VRAM)", menu)
        self.act_model.triggered.connect(self._toggle_model)
        menu.addAction(self.act_model)
        a = QAction("Quit (unloads the model)", menu)
        a.triggered.connect(self.quit)
        menu.addAction(a)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()
        self._orig_show = self.window.show_window
        self.window.show_window = self._show_with_litany  # first show after start plays the litany

        # The model is loaded while the tray runs and unloaded when it quits.
        self.model = ModelService(self)
        self.model.state_changed.connect(self._model_state)
        self.model.ensure_loaded()
        self._model_state(self.model.state)

    def _model_state(self, state: str) -> None:
        label = {"ready": "Unload model (free VRAM)", "waking": "Model is loading…", "unloaded": "Load model",
                 "unknown": "Model: not managed here"}[state]
        self.act_model.setText(label)
        self.act_model.setEnabled(state in ("ready", "unloaded"))
        self.tray.setToolTip({"ready": "Mikronous — model loaded", "waking": "Mikronous — model loading",
                              "unloaded": "Mikronous — model unloaded", "unknown": "Mikronous"}[state])
        self.window.set_model_state(state)

    def _toggle_model(self) -> None:
        if self.model.state == "ready":
            self.model.unload()
        else:
            self.model.ensure_loaded()

    def _show_with_litany(self) -> None:
        self._orig_show()
        if self._litany_pending:
            self._litany_pending = False
            self.window.play_litany()

    # ------------------------------------------------------------------ slots
    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick, QSystemTrayIcon.MiddleClick):
            self.window.toggle()

    def _control_conn(self) -> None:
        while self.control.hasPendingConnections():
            sock = self.control.nextPendingConnection()
            sock.waitForReadyRead(500)
            cmd = bytes(sock.readAll().data()).decode(errors="replace").strip()
            sock.deleteLater()
            if cmd == "toggle":
                self.window.toggle()
            elif cmd == "show":
                self.window.show_window()
            elif cmd == "hide":
                self.window.hide_window()
            elif cmd == "new":
                self.window.new_chat()
                self.window.show_window()
            elif cmd == "quit":
                self.quit()

    def _inbox_message(self, payload: dict) -> None:
        self.window.show_inbox_message(payload)
        # The plugin already raised a KDE notification; only nudge when the window is hidden.
        if not self.window.isVisible():
            self.tray.showMessage(payload.get("title") or "Mikronous", payload.get("text", "")[:200],
                                  QSystemTrayIcon.Information, 5000)

    def _open_notes(self) -> None:
        from pathlib import Path
        d = Path(settings.load().get("notes_dir", "~/Mikronous/notes")).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _status(self) -> None:
        h = self.client.health()
        text = f"Gateway error: {h['error']}" if "error" in h else f"Gateway OK at {self.client.v1}"
        self.tray.showMessage("Mikronous", text, QSystemTrayIcon.Information, 4000)

    def quit(self) -> None:
        self.window.stop()
        if self.model.unload_on_quit():
            self.model.unload()          # frees the VRAM; MIKRONOUS_KEEP_MODEL=1 keeps it resident
        self.inbox.stop()
        self.control.close()
        QLocalServer.removeServer(settings.CONTROL_SERVER)
        self.client.close()
        self.app.quit()
