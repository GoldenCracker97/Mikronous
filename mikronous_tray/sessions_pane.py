"""The past-chats pane: the tray's earlier sessions, fetched from the gateway's /api/sessions.

Only sessions the tray created (ids starting with ``tray-``) are listed; `mik ask` and cron sessions
stay out of the way. Fetching runs on a thread so an unreachable gateway never freezes the window.
"""

from __future__ import annotations

import datetime as _dt
import time

from PySide6.QtCore import QPoint, Qt, QThread, Signal
from PySide6.QtWidgets import (QFrame, QInputDialog, QLabel, QListView, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPushButton, QVBoxLayout)

TRAY_PREFIX = "tray-"
PANE_WIDTH = 220


def session_label(s: dict, now: float | None = None) -> tuple[str, str]:
    """(first line, second line) for a session row: its title or first message, then when and how long."""
    title = (s.get("title") or s.get("preview") or "").strip().replace("\n", " ")
    if not title:
        title = "(empty rite)"
    if len(title) > 48:
        title = title[:45].rstrip() + "…"
    when = _when(s.get("last_active") or s.get("started_at"), now)
    count = s.get("message_count")
    second = when + (f" · {int(count)} msgs" if isinstance(count, (int, float)) and count else "")
    return title, second


def _when(value, now: float | None) -> str:
    now = time.time() if now is None else now
    ts: float | None = None
    if isinstance(value, (int, float)):
        ts = float(value)
    elif isinstance(value, str) and value:
        try:
            ts = float(value)
        except ValueError:
            try:
                ts = _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return value[:16]
    if ts is None:
        return ""
    d = _dt.datetime.fromtimestamp(ts)
    age = now - ts
    if age < 86_400 and d.date() == _dt.datetime.fromtimestamp(now).date():
        return d.strftime("today %H:%M")
    if age < 7 * 86_400:
        return d.strftime("%a %H:%M")
    return d.strftime("%Y-%m-%d")


class _Fetcher(QThread):
    done = Signal(list)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            rows = self.client.list_sessions(100)
        except Exception:  # noqa: BLE001
            rows = []
        self.done.emit(list(rows))


class SessionsPane(QFrame):
    open_session = Signal(str)
    new_chat = Signal()

    def __init__(self, client, parent=None):
        super().__init__(parent, objectName="sessions")
        self.client = client
        self.current = ""
        self._fetcher: _Fetcher | None = None
        self.setFixedWidth(PANE_WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(QLabel("PAST RITES", objectName="paneTitle"))
        self.list = QListWidget(objectName="sessionList")
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListView.Adjust)          # re-wrap items to the pane width
        self.list.setUniformItemSizes(False)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideNone)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemClicked.connect(self._clicked)
        lay.addWidget(self.list, 1)
        b = QPushButton("+ NEW RITE", objectName="paneNew")
        b.clicked.connect(self.new_chat.emit)
        lay.addWidget(b)

    # ------------------------------------------------------------------ data
    def refresh(self) -> None:
        if self._fetcher is not None and self._fetcher.isRunning():
            return
        self._fetcher = _Fetcher(self.client, self)
        self._fetcher.done.connect(self._populate)
        self._fetcher.start()

    def _populate(self, rows: list) -> None:
        self.list.clear()
        for s in rows:
            sid = str(s.get("id") or "")
            if not sid.startswith(TRAY_PREFIX):
                continue
            title, second = session_label(s)
            item = QListWidgetItem(f"{title}\n{second}")
            item.setData(Qt.UserRole, sid)
            item.setToolTip(sid)
            self.list.addItem(item)
        self.set_current(self.current)

    def set_current(self, session_id: str) -> None:
        self.current = session_id
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.UserRole) == session_id:
                self.list.setCurrentItem(item)
                return
        self.list.setCurrentItem(None)

    def session_ids(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    # ------------------------------------------------------------------ interaction
    def _clicked(self, item: QListWidgetItem) -> None:
        sid = item.data(Qt.UserRole)
        if sid and sid != self.current:
            self.open_session.emit(sid)

    def _menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        sid = item.data(Qt.UserRole)
        menu = QMenu(self)
        a_open = menu.addAction("Open")
        a_rename = menu.addAction("Rename…")
        a_delete = menu.addAction("Delete")
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen is a_open:
            self.open_session.emit(sid)
        elif chosen is a_rename:
            self.rename(sid, item.text().split("\n", 1)[0])
        elif chosen is a_delete:
            self.delete(sid)

    def rename(self, sid: str, old: str) -> None:
        title, ok = QInputDialog.getText(self, "Rename rite", "Title:", text="" if old.startswith("(") else old)
        if not ok:
            return
        try:
            self.client.rename_session(sid, title.strip())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Mikronous", f"Could not rename: {exc}")
            return
        self.refresh()

    def delete(self, sid: str) -> None:
        if QMessageBox.question(self, "Delete rite", "Delete this chat and its history? This cannot be undone.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.client.delete_session(sid)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Mikronous", f"Could not delete: {exc}")
            return
        if sid == self.current:
            self.new_chat.emit()
        self.refresh()
