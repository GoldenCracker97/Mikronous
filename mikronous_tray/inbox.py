"""Receive messages the Hermes plugin pushes (reminders, cron output) over a Unix socket.

The plugin side (``deliver.py``) connects to ``$XDG_RUNTIME_DIR/mikronous.sock`` and writes one
JSON line per message. ``InboxServer`` listens there with a ``QLocalServer`` and emits
``message(dict)`` on the Qt main thread.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .settings import socket_path


class InboxServer(QObject):
    message = Signal(dict)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._buffers: dict[QLocalSocket, bytes] = {}

    def start(self) -> bool:
        path = str(socket_path())
        QLocalServer.removeServer(path)          # stale socket from a previous run
        if not self._server.listen(path):
            return False
        self._server.newConnection.connect(self._accept)
        return True

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._close(s))

    def _read(self, sock: QLocalSocket) -> None:
        self._buffers[sock] = self._buffers.get(sock, b"") + bytes(sock.readAll().data())
        while b"\n" in self._buffers[sock]:
            line, _, rest = self._buffers[sock].partition(b"\n")
            self._buffers[sock] = rest
            self._emit(line)

    def _close(self, sock: QLocalSocket) -> None:
        tail = self._buffers.pop(sock, b"")
        if tail.strip():
            self._emit(tail)
        sock.deleteLater()

    def _emit(self, line: bytes) -> None:
        try:
            obj = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            return
        if isinstance(obj, dict) and obj.get("text"):
            self.message.emit(obj)

    def stop(self) -> None:
        self._server.close()
        QLocalServer.removeServer(str(socket_path()))
