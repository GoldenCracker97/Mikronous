"""The chat window: transcript, streaming reply, tool activity, approval card, input box."""

from __future__ import annotations

import html
import threading

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy,
                               QTextBrowser, QVBoxLayout, QWidget)

from . import settings
from .hermes_client import GatewayError, HermesClient, RunEvent

APPROVAL_LABELS = {"once": "Allow once", "session": "Allow this session", "always": "Always allow", "deny": "Deny"}


# ------------------------------------------------------------------------------------- worker
class ChatWorker(QObject):
    """Runs one turn on a background thread and relays run events as signals."""
    started_run = Signal(str)                 # run_id
    delta = Signal(str)
    interim = Signal(str)
    tool_started = Signal(str, str)           # tool, preview
    tool_completed = Signal(str, bool, str)   # tool, error, preview
    approval = Signal(dict)
    finished = Signal(str, str)               # status, output/error text

    def __init__(self, client: HermesClient, text: str, session_id: str):
        super().__init__()
        self.client, self.text, self.session_id = client, text, session_id
        self.run_id: str | None = None
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()
        if self.run_id:
            try:
                self.client.stop(self.run_id)
            except GatewayError:
                pass

    @Slot()
    def run(self) -> None:
        try:
            self.run_id = self.client.start_run(self.text, self.session_id)
            self.started_run.emit(self.run_id)
            for ev in self.client.events(self.run_id, should_stop=self._stop.is_set):
                self._dispatch(ev)
                if ev.terminal:
                    return
            self.finished.emit("cancelled", "Stopped.")
        except GatewayError as exc:
            self.finished.emit("failed", str(exc))
        except Exception as exc:  # noqa: BLE001
            self.finished.emit("failed", f"{exc.__class__.__name__}: {exc}")

    def _dispatch(self, ev: RunEvent) -> None:
        d = ev.data
        if ev.name == "message.delta":
            self.delta.emit(str(d.get("delta", "")))
        elif ev.name == "message.interim":
            if not d.get("already_streamed"):
                self.interim.emit(str(d.get("text", "")))
        elif ev.name == "tool.started":
            self.tool_started.emit(str(d.get("tool", "")), str(d.get("preview") or ""))
        elif ev.name == "tool.completed":
            self.tool_completed.emit(str(d.get("tool", "")), bool(d.get("error")), str(d.get("preview") or ""))
        elif ev.name == "approval.request":
            self.approval.emit(d)
        elif ev.name == "run.completed":
            self.finished.emit("completed", str(d.get("output") or ""))
        elif ev.name in ("run.failed", "run.interrupted"):
            self.finished.emit("failed", str(d.get("error") or d.get("output") or "The run failed."))
        elif ev.name == "run.cancelled":
            self.finished.emit("cancelled", str(d.get("output") or "Stopped."))


# ------------------------------------------------------------------------------------- input box
class InputBox(QPlainTextEdit):
    submit = Signal()
    escape = Signal()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submit.emit()
            return
        if e.key() == Qt.Key_Escape:
            self.escape.emit()
            return
        super().keyPressEvent(e)


# ------------------------------------------------------------------------------------- window
class ChatWindow(QWidget):
    hidden_by_user = Signal()

    def __init__(self, client: HermesClient):
        super().__init__()
        self.client = client
        self.state = settings.load()
        self.session_id = self.state.get("session_id") or ""
        self.messages: list[dict] = []        # {"role": user|assistant|system|tool, "text": str}
        self._streaming: str | None = None    # assistant text being streamed
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._pending_approval: dict | None = None
        self._render_timer = QTimer(self, interval=60, singleShot=True)
        self._render_timer.timeout.connect(self._render)

        self.setWindowTitle("Mikronous")
        self.setWindowFlag(Qt.Dialog, True)   # keeps it out of the taskbar on most Plasma setups
        w = self.state.get("window", {})
        self.resize(int(w.get("width", 520)), int(w.get("height", 680)))
        self._build()
        if self.session_id:
            self._load_history()
        else:
            self.new_chat()

    # ------------------------------------------------------------------ ui
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setFrameShape(QFrame.NoFrame)
        root.addWidget(self.view, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: palette(mid); font-size: 11px;")
        root.addWidget(self.status)

        self.approval_card = QFrame()
        self.approval_card.setFrameShape(QFrame.StyledPanel)
        self.approval_card.setStyleSheet("QFrame { border: 1px solid palette(highlight); border-radius: 6px; padding: 6px; }")
        card = QVBoxLayout(self.approval_card)
        self.approval_text = QLabel("")
        self.approval_text.setWordWrap(True)
        self.approval_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.addWidget(self.approval_text)
        self.approval_buttons = QHBoxLayout()
        card.addLayout(self.approval_buttons)
        self.approval_card.hide()
        root.addWidget(self.approval_card)

        row = QHBoxLayout()
        self.input = InputBox()
        self.input.setPlaceholderText("Ask Mikronous…  (Enter to send, Shift+Enter for a new line, Esc to hide)")
        self.input.setFixedHeight(72)
        self.input.submit.connect(self.send)
        self.input.escape.connect(self.hide_window)
        row.addWidget(self.input, 1)
        col = QVBoxLayout()
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        col.addWidget(self.send_btn)
        col.addWidget(self.stop_btn)
        row.addLayout(col)
        root.addLayout(row)

    # ------------------------------------------------------------------ transcript
    def _render(self) -> None:
        parts: list[str] = []
        for m in self.messages:
            parts.append(_render_message(m))
        if self._streaming is not None:
            parts.append(_render_message({"role": "assistant", "text": self._streaming or "…"}))
        self.view.setHtml("<style>p{margin:0 0 6px 0}</style>" + "".join(parts))
        self.view.moveCursor(QTextCursor.End)

    def _schedule_render(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _add(self, role: str, text: str) -> None:
        self.messages.append({"role": role, "text": text})
        self._schedule_render()

    def _load_history(self) -> None:
        for m in self.client.session_messages(self.session_id):
            self.messages.append({"role": m["role"], "text": m["content"]})
        self._render()
        self._set_status(f"session {self.session_id}")

    def new_chat(self) -> None:
        if self._worker:
            self.stop()
        from .hermes_client import new_session_id
        self.session_id = new_session_id()
        settings.save(session_id=self.session_id)
        self.messages = []
        self._streaming = None
        self._render()
        self._set_status("new chat")

    def show_inbox_message(self, payload: dict) -> None:
        title = payload.get("title") or "Mikronous"
        self._add("inbox", f"**{title}**  \n{payload.get('text', '')}")

    # ------------------------------------------------------------------ sending
    @Slot()
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self._worker is not None:
            return
        self.input.clear()
        self._add("user", text)
        self._streaming = ""
        self._schedule_render()
        self._start_worker(text)

    def _start_worker(self, text: str) -> None:
        self._thread = QThread(self)
        self._worker = ChatWorker(self.client, text, self.session_id)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.delta.connect(self._on_delta)
        self._worker.interim.connect(self._on_interim)
        self._worker.tool_started.connect(self._on_tool_started)
        self._worker.tool_completed.connect(self._on_tool_completed)
        self._worker.approval.connect(self._on_approval)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_worker)
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_status("thinking…")
        self._thread.start()

    @Slot()
    def stop(self) -> None:
        if self._worker:
            self._worker.request_stop()
            self._set_status("stopping…")

    @Slot()
    def _cleanup_worker(self) -> None:
        if self._thread:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.input.setFocus()

    # ------------------------------------------------------------------ events
    @Slot(str)
    def _on_delta(self, delta: str) -> None:
        self._streaming = (self._streaming or "") + delta
        self._schedule_render()

    @Slot(str)
    def _on_interim(self, text: str) -> None:
        if text.strip():
            self._add("assistant", text.strip())

    @Slot(str, str)
    def _on_tool_started(self, tool: str, preview: str) -> None:
        self._set_status(f"{tool} …")
        self._add("tool", f"{tool} {preview}".strip())

    @Slot(str, bool, str)
    def _on_tool_completed(self, tool: str, error: bool, preview: str) -> None:
        self._set_status("thinking…")
        if error:
            self._add("tool", f"{tool} failed: {preview[:200]}")

    @Slot(dict)
    def _on_approval(self, data: dict) -> None:
        self._pending_approval = data
        cmd = data.get("command") or data.get("tool_name") or "a tool call"
        desc = data.get("description") or "The assistant wants to run something that needs your approval."
        self.approval_text.setText(f"<b>Approve?</b> {html.escape(str(desc))}<br><code>{html.escape(str(cmd))}</code>")
        while self.approval_buttons.count():
            item = self.approval_buttons.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for choice in data.get("choices") or ["once", "deny"]:
            b = QPushButton(APPROVAL_LABELS.get(choice, choice))
            b.clicked.connect(lambda _=False, c=choice: self._resolve_approval(c))
            self.approval_buttons.addWidget(b)
        self.approval_card.show()
        self._set_status("waiting for your approval")
        self.show_window()

    def _resolve_approval(self, choice: str) -> None:
        data, self._pending_approval = self._pending_approval, None
        self.approval_card.hide()
        if not data or not self._worker or not self._worker.run_id:
            return
        try:
            self.client.approve(self._worker.run_id, choice, data.get("request_id"))
            self._add("tool", f"approval: {APPROVAL_LABELS.get(choice, choice)}")
            self._set_status("thinking…")
        except GatewayError as exc:
            self._add("system", f"Could not send approval: {exc}")

    @Slot(str, str)
    def _on_finished(self, status: str, text: str) -> None:
        self.approval_card.hide()
        streamed = (self._streaming or "").strip()
        self._streaming = None
        if status == "completed":
            final = text.strip() or streamed
            if final:
                self._add("assistant", final)
        elif status == "cancelled":
            if streamed:
                self._add("assistant", streamed)
            self._add("system", "Stopped.")
        else:
            if streamed:
                self._add("assistant", streamed)
            self._add("system", f"Error: {text}")
        self._set_status("")
        self._schedule_render()

    # ------------------------------------------------------------------ misc
    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def hide_window(self) -> None:
        self.hide()
        self.hidden_by_user.emit()

    def toggle(self) -> None:
        if self.isVisible() and self.isActiveWindow():
            self.hide_window()
        else:
            self.show_window()

    def closeEvent(self, e) -> None:  # noqa: N802 - close hides; Quit is in the tray menu
        settings.save(window={"width": self.width(), "height": self.height()})
        e.ignore()
        self.hide_window()


def _render_message(m: dict) -> str:
    role, text = m.get("role"), m.get("text", "")
    if role == "user":
        return f'<div style="margin:8px 0 4px 0;"><b>You</b></div><div style="margin-left:8px;">{_md(text)}</div>'
    if role == "assistant":
        return f'<div style="margin:8px 0 4px 0;"><b>Mikronous</b></div><div style="margin-left:8px;">{_md(text)}</div>'
    if role == "inbox":
        return f'<div style="margin:8px 0; padding:6px; border-left:3px solid palette(highlight);">{_md(text)}</div>'
    if role == "tool":
        return f'<div style="margin-left:8px; color:gray; font-size:11px;">⚙ {html.escape(text)}</div>'
    return f'<div style="margin-left:8px; color:gray; font-style:italic;">{html.escape(text)}</div>'


def _md(text: str) -> str:
    """Markdown → HTML via Qt's own converter (no extra dependency)."""
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setMarkdown(text)
    body = doc.toHtml()
    # keep only the body content so our wrapper styles apply
    start, end = body.find("<body"), body.rfind("</body>")
    if start != -1 and end != -1:
        body = body[body.find(">", start) + 1:end]
    return body
