"""The chat window (the data-slate): transcript, streaming reply, rite chips, sanction card, input."""

from __future__ import annotations

import html
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QKeyEvent, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

from . import prefs, selection, settings, theme
from .hermes_client import GatewayError, HermesClient, RunEvent
from .sessions_pane import PANE_WIDTH, SessionsPane
from .theme import APPROVAL_LABELS, CANT, TOKENS


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

    def __init__(self, client: HermesClient, text: str, session_id: str, image_paths: list[str] | None = None):
        super().__init__()
        self.client, self.text, self.session_id = client, text, session_id
        self.image_paths = list(image_paths or [])
        self.run_id: str | None = None
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()
        if self.run_id:
            try:
                self.client.stop(self.run_id)
            except GatewayError:
                pass

    def _got_run_id(self, run_id: str | None) -> None:
        self.run_id = run_id or None
        if self.run_id:
            self.started_run.emit(self.run_id)
            if self._stop.is_set():              # CEASE came before the id was known: tell the gateway now
                try:
                    self.client.stop(self.run_id)
                except GatewayError:
                    pass

    def _terminal(self, ev: RunEvent) -> None:
        """A terminal event after CEASE is a cancellation whatever the gateway called it."""
        if self._stop.is_set():
            self.finished.emit("cancelled", str(ev.data.get("output") or "Stopped."))
        else:
            self._dispatch(ev)

    @Slot()
    def run(self) -> None:
        try:
            if self.image_paths:                 # images ride the session chat stream (the runs API is text-only)
                for ev in self.client.session_chat_events(self.session_id, self.text, self.image_paths, should_stop=self._stop.is_set):
                    if ev.name == "run.started":
                        self._got_run_id(str(ev.data.get("run_id") or ""))
                        continue
                    if ev.terminal:
                        self._terminal(ev)
                        return
                    self._dispatch(ev)
                self.finished.emit("cancelled", "Stopped.")
                return
            self._got_run_id(self.client.start_run(self.text, self.session_id))
            for ev in self.client.events(self.run_id, should_stop=self._stop.is_set):
                if ev.terminal:
                    self._terminal(ev)
                    return
                self._dispatch(ev)
            self.finished.emit("cancelled", "Stopped.")
        except GatewayError as exc:
            self.finished.emit("cancelled" if self._stop.is_set() else "failed", str(exc))
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
def paths_from_mime(mime) -> list[str]:
    """Local file/folder paths from a drag's mime data (file:// URLs), in drop order."""
    if not mime.hasUrls():
        return []
    return [os.path.normpath(u.toLocalFile()) for u in mime.urls() if u.isLocalFile() and u.toLocalFile()]


def quote_paths(paths: list[str]) -> str:
    """How dropped paths are written into the message: one per line, quoted, so the agent reads them verbatim."""
    return "\n".join(f'"{p}"' for p in paths)


class InputBox(QPlainTextEdit):
    submit = Signal()
    escape = Signal()
    dropped = Signal(list)                    # local paths

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:  # noqa: N802
        if paths_from_mime(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if paths_from_mime(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e: QDropEvent) -> None:  # noqa: N802
        paths = paths_from_mime(e.mimeData())
        if paths:
            e.acceptProposedAction()
            self.dropped.emit(paths)
        else:
            super().dropEvent(e)

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
    update_requested = Signal()          # the UPDATE button; the tray app owns the checker
    hotkey_changed = Signal(str)         # Windows: re-register after Settings
    answer_ready = Signal(str, str)      # (question, answer) for a turn started while the window was hidden

    def __init__(self, client: HermesClient):
        super().__init__()
        self.client = client
        self.fonts = theme.load_fonts()
        self.state = settings.load()
        self.session_id = self.state.get("session_id") or ""
        self.messages: list[dict] = []        # {"role": user|assistant|system|tool|inbox|litany, "text": str}
        self._streaming: str | None = None    # assistant text being streamed
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._pending_approval: dict | None = None
        self._litany_done = False
        self._render_timer = QTimer(self, interval=60, singleShot=True)
        self._render_timer.timeout.connect(self._render)
        self._stall_timer = QTimer(self, interval=90_000, singleShot=True)   # nothing arrived for 90 s
        self._stall_timer.timeout.connect(lambda: self._set_status(CANT["stall"]))
        self._settings_thread: QThread | None = None
        self._update_state = "idle"
        self._copy_next_answer = False
        self._quiet_question: str | None = None
        self._pending_images: list[str] = []
        self._zombies: list[tuple[QThread, ChatWorker]] = []   # detached turns still winding down (ignored, then freed)
        self._recorder = None
        self._stt_thread: QThread | None = None
        self._speaker = None
        self._tts_thread: threading.Thread | None = None

        self.setObjectName("chatRoot")
        self.setWindowTitle("Mikronous")
        self.setWindowIcon(theme.qicon("color"))
        self.setWindowFlag(Qt.Dialog, True)   # keeps it out of the taskbar on most Plasma setups
        self.setFocusPolicy(Qt.StrongFocus)
        w = self.state.get("window", {})
        self.resize(int(w.get("width", 520)), int(w.get("height", 680)))
        self._build()
        self.setStyleSheet(theme.stylesheet(self.fonts))
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.hide_window)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_chat)
        QShortcut(QKeySequence("Ctrl+H"), self, activated=self.toggle_sidebar)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.open_settings)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, activated=self.capture_screen)
        if self.session_id:
            self._load_history()
        else:
            self.new_chat()
        if self.state.get("sidebar"):
            self.toggle_sidebar(True, resize=False)

    # ------------------------------------------------------------------ ui
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(objectName="header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 7, 12, 7)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        hl.addWidget(self.dot)
        hl.addSpacing(4)
        hl.addWidget(QLabel("MIKRONOUS", objectName="title"))
        hl.addStretch(1)
        self.model_label = QLabel(_model_name() + (" · 👁" if _vision_ready() else ""), objectName="model")
        hl.addWidget(self.model_label)
        hl.addSpacing(10)
        self.btn_chats = QPushButton("CHATS", objectName="hbtn")
        self.btn_chats.setCheckable(True)
        self.btn_chats.setToolTip("Past chats (Ctrl+H)")
        self.btn_chats.clicked.connect(lambda checked: self.toggle_sidebar(checked))
        hl.addWidget(self.btn_chats)
        self.btn_settings = QPushButton("SETTINGS", objectName="hbtn")
        self.btn_settings.setToolTip("Settings (Ctrl+,)")
        self.btn_settings.clicked.connect(lambda: self.open_settings())
        hl.addWidget(self.btn_settings)
        self.btn_screen = QPushButton("SCREEN", objectName="hbtn")
        self.btn_screen.setToolTip("Capture a region of the screen and ask about it (Ctrl+Shift+S)")
        self.btn_screen.clicked.connect(self.capture_screen)
        hl.addWidget(self.btn_screen)
        self.btn_update = QPushButton("UPDATE", objectName="hbtn")
        self.btn_update.setToolTip("Check GitHub for a newer Mikronous")
        self.btn_update.clicked.connect(self.update_requested.emit)
        hl.addWidget(self.btn_update)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)
        self.pane = SessionsPane(self.client)
        self.pane.open_session.connect(self.open_session)
        self.pane.new_chat.connect(self.new_chat)
        self.pane.hide()
        body.addWidget(self.pane)

        inner = QVBoxLayout()
        inner.setContentsMargins(10, 8, 10, 10)
        inner.setSpacing(8)
        body.addLayout(inner, 1)

        self.view = QTextBrowser(objectName="view")
        self.view.setOpenExternalLinks(True)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.document().setDefaultStyleSheet(theme.transcript_css(self.fonts))
        inner.addWidget(self.view, 1)

        self.status = QLabel("", objectName="status")
        inner.addWidget(self.status)

        self.approval_card = QFrame(objectName="approval")
        card = QVBoxLayout(self.approval_card)
        card.setContentsMargins(10, 8, 10, 8)
        self.approval_text = QLabel("", objectName="approvalText")
        self.approval_text.setWordWrap(True)
        self.approval_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.addWidget(self.approval_text)
        self.approval_buttons = QHBoxLayout()
        card.addLayout(self.approval_buttons)
        self.approval_card.hide()
        inner.addWidget(self.approval_card)

        row = QHBoxLayout()
        self.input = InputBox(objectName="input")
        self.input.setPlaceholderText("> query the machine spirit_   (Enter transmits · Shift+Enter new line · drop files here · Esc hides)")
        self.input.setFixedHeight(72)
        self.input.submit.connect(self.send)
        self.input.escape.connect(self.hide_window)
        self.input.dropped.connect(self.attach_paths)
        self.view.viewport().setAcceptDrops(True)
        self.view.viewport().installEventFilter(self)      # drops on the transcript land in the input too
        row.addWidget(self.input, 1)
        col = QVBoxLayout()
        self.send_btn = QPushButton("TRANSMIT", objectName="send")
        self.send_btn.clicked.connect(self.send)
        self.stop_btn = QPushButton("CEASE", objectName="stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        self.vox_btn = QPushButton("VOX", objectName="stop")
        self.vox_btn.setToolTip("Hold to speak; release to transcribe (or tap the voice key)")
        self.vox_btn.pressed.connect(self.vox_start)
        self.vox_btn.released.connect(self.vox_stop)
        col.addWidget(self.send_btn)
        col.addWidget(self.stop_btn)
        col.addWidget(self.vox_btn)
        row.addLayout(col)
        inner.addLayout(row)
        self._set_dot("idle")

    def _set_dot(self, state: str) -> None:
        colour = {"idle": TOKENS["phosphor"], "thinking": TOKENS["brass"], "approval": TOKENS["red"]}.get(state, TOKENS["muted"])
        self.dot.setStyleSheet(f"background: {colour}; border-radius: 5px;")
        self.dot.setToolTip({"idle": "idle", "thinking": "cogitating", "approval": "awaiting sanction"}.get(state, state))

    # ------------------------------------------------------------------ transcript
    def _render(self) -> None:
        parts = [_render_message(m) for m in self.messages]
        if self._streaming is not None:
            parts.append(_render_message({"role": "assistant", "text": self._streaming or "…"}))
        self.view.setHtml("".join(parts))
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
        self._set_status(CANT["session"])
        self.pane.set_current(self.session_id)

    def new_chat(self) -> None:
        self._detach_worker()
        from .hermes_client import new_session_id
        self.session_id = new_session_id()
        settings.save(session_id=self.session_id)
        self.messages = []
        self._streaming = None
        self._pending_images = []
        self._render()
        self._set_status(CANT["new"])
        self.pane.set_current(self.session_id)

    # ------------------------------------------------------------------ past chats
    def toggle_sidebar(self, show: bool | None = None, *, resize: bool = True) -> None:
        show = not self.pane.isVisible() if show is None else bool(show)
        if show == self.pane.isVisible():
            self.btn_chats.setChecked(show)
            return
        if resize and self.isVisible():
            self.resize(self.width() + (PANE_WIDTH if show else -PANE_WIDTH), self.height())
        self.pane.setVisible(show)
        self.btn_chats.setChecked(show)
        settings.save(sidebar=show)
        if show:
            self.pane.refresh()

    def open_session(self, session_id: str) -> None:
        """Switch the slate to an earlier chat; history comes from the gateway."""
        if not session_id or session_id == self.session_id:
            return
        self._detach_worker()
        self.session_id = session_id
        settings.save(session_id=session_id)
        self.messages = []
        self._streaming = None
        self._pending_images = []
        self._load_history()
        self._set_status(CANT["session_opened"])
        self.input.setFocus()

    # ------------------------------------------------------------------ settings
    def open_settings(self, tab: str = "slate") -> None:
        from .settings_dialog import SettingsDialog
        if self._settings_thread is not None:
            return                                   # a previous save is still restarting the gateway
        old = prefs.read()
        dlg = SettingsDialog(self, old, model=_model_name(), kde_shortcut=prefs.kde_shortcut(),
                             kde_shortcut_selection=prefs.kde_shortcut("selection"), kde_shortcut_vox=prefs.kde_shortcut("vox"),
                             client=self.client)
        if tab == "routines" and dlg.routines is not None:
            dlg.tabs.setCurrentIndex(1)
        dlg.setStyleSheet(self.styleSheet())
        if dlg.exec() != SettingsDialog.Accepted:
            return
        new = dlg.values()
        try:
            changed = prefs.apply(old, new)
        except (ValueError, OSError) as exc:
            self._add("system", f"++ SETTINGS NOT INSCRIBED ++ {exc}")
            self._render()
            return
        if not changed:
            return
        if any(k in changed for k in ("hotkey", "hotkey_selection", "hotkey_vox")):
            self.hotkey_changed.emit(new.hotkey)
        if prefs.needs_gateway_restart(changed):
            self._set_status(CANT["gateway_restarting"])
            self._restart_gateway_async()
        else:
            self._set_status(CANT["settings_saved"])

    def _restart_gateway_async(self) -> None:
        worker = _GatewayRestart()
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_gateway_restarted)
        worker.done.connect(thread.quit)
        thread.finished.connect(lambda: setattr(self, "_settings_thread", None))
        thread.finished.connect(thread.deleteLater)
        self._settings_thread = thread
        self._gateway_worker = worker                # keep a reference while the thread runs
        thread.start()

    @Slot(bool, str)
    def _on_gateway_restarted(self, ok: bool, message: str) -> None:
        if ok:
            self._set_status(CANT["gateway_restarted"])
        else:
            self._add("system", f"++ GATEWAY DID NOT RETURN ++ {message}")
            self._set_status("")
        self._render()

    # ------------------------------------------------------------------ voice
    def vox_toggle(self) -> None:
        """The voice key: tap to start, tap again to stop (global shortcuts have no key-up)."""
        if self._recorder is None:
            self.show_window()
            self.vox_start()
        else:
            self.vox_stop()

    def vox_start(self) -> None:
        from . import voice_io
        if self._recorder is not None or self._stt_thread is not None:
            return
        ok, msg = voice_io.stt_available()
        if not ok:
            self._set_status(f"++ VOX UNAVAILABLE · {msg.upper()} ++")
            return
        rec = voice_io.Recorder()
        if not rec.start():
            self._set_status(f"++ VOX FAILED · {rec.error.upper()} ++")
            return
        self._recorder = rec
        self.vox_btn.setText("…")
        self._set_dot("approval")
        self._set_status(CANT["listening"])

    def vox_stop(self) -> None:
        rec, self._recorder = self._recorder, None
        self.vox_btn.setText("VOX")
        self._set_dot("idle")
        if rec is None:
            return
        path = rec.stop()
        if not path:
            try:
                os.remove(rec.path)
            except OSError:
                pass
            self._set_status(CANT["heard_nothing"])
            return
        self._set_status(CANT["transcribing"])
        worker = _Transcribe(path, settings.load().get("stt_model") or "base")
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_transcribed)
        worker.done.connect(thread.quit)
        thread.finished.connect(lambda: setattr(self, "_stt_thread", None))
        thread.finished.connect(thread.deleteLater)
        self._stt_thread, self._stt_worker = thread, worker
        thread.start()

    @Slot(str, str)
    def _on_transcribed(self, text: str, error: str) -> None:
        if error:
            self._add("system", f"++ VOX MALFUNCTION ++ {error}")
            self._set_status("")
            self._render()
            return
        if not text:
            self._set_status(CANT["heard_nothing"])
            return
        cur = self.input.toPlainText().rstrip()
        self.input.setPlainText((cur + " " if cur else "") + text)
        self.input.moveCursor(QTextCursor.End)
        self.input.setFocus()
        self._set_status(CANT["heard"])

    def speak(self, text: str) -> None:
        """Read a reply aloud on a thread when Settings → Voice output is on."""
        from . import voice_io
        if not settings.load().get("tts"):
            return
        if self._speaker is None:
            self._speaker = voice_io.Speaker()
        self._speaker.stop()
        voice = settings.load().get("tts_voice") or voice_io.DEFAULT_TTS_VOICE
        effect = settings.load().get("tts_effect") or voice_io.DEFAULT_TTS_EFFECT

        def run():
            try:
                self._speaker.say(_plain_text(text), voice, effect)
            except Exception as exc:  # noqa: BLE001 - say why in the status line, never crash the UI
                print(f"mikronous-tray: speech failed: {exc}", file=sys.stderr)
        self._tts_thread = threading.Thread(target=run, name="mikronous-tts", daemon=True)
        self._tts_thread.start()

    def ask_quietly(self, text: str) -> None:
        """A question from KRunner: new chat, run it, and when the window is hidden hand the answer to the tray
        (notification) through ``answer_ready`` instead of raising the window."""
        text = (text or "").strip()
        if not text:
            return
        self.new_chat()
        self._quiet_question = text
        self._add("user", text)
        self._streaming = ""
        self._schedule_render()
        self._start_worker(text)

    # ------------------------------------------------------------------ selected-text actions
    def selection_menu(self, text: str | None = None) -> None:
        """Grab the highlighted text (or use ``text``) and offer the actions at the cursor."""
        text = selection.grab_selection() if text is None else text
        if not text or not text.strip():
            self.show_window()
            self._set_status(CANT["no_selection"])
            return
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        lang = settings.load().get("translate_lang") or "English"
        menu = QMenu()
        menu.setStyleSheet(self.styleSheet())
        for a in selection.ACTIONS:
            menu.addAction(selection.label(a, lang), lambda a=a: self.run_selection_action(a.key, text))
        menu.exec(QCursor.pos())

    def run_selection_action(self, key: str, text: str) -> None:
        a = selection.action(key)
        lang = settings.load().get("translate_lang") or "English"
        self.new_chat()
        self.show_window()
        if a.prompt is None:                       # "Ask about it": hand the text over, let the user type the question
            self.input.setPlainText(selection.build_prompt(key, text) + "\n")
            self.input.moveCursor(QTextCursor.End)
            self.input.setFocus()
            return
        self._copy_next_answer = a.copy_result
        self._add("user", f"{selection.label(a, lang)}: {text.strip()[:400]}{'…' if len(text.strip()) > 400 else ''}")
        self._streaming = ""
        self._schedule_render()
        self._start_worker(selection.build_prompt(key, text, lang))

    # ------------------------------------------------------------------ update button
    def set_update_state(self, state: str, detail: str = "") -> None:
        """idle | checking | current | available | failed | running — drives the UPDATE button and status line."""
        self._update_state = state
        alert = state == "available"
        self.btn_update.setText("UPDATE •" if alert else "UPDATE")
        self.btn_update.setProperty("alert", "true" if alert else "false")
        self.btn_update.style().unpolish(self.btn_update)
        self.btn_update.style().polish(self.btn_update)
        self.btn_update.setEnabled(state not in ("checking", "running"))
        self.btn_update.setToolTip(detail or "Check GitHub for a newer Mikronous")
        cant = CANT.get(f"update_{state}")
        if cant and self._worker is None:
            self._set_status(cant + (f" · {detail}" if detail and state in ("available", "failed") else ""))

    def attach_paths(self, paths: list[str]) -> None:
        """Dropped files/folders: quote them into the input so the next message carries them."""
        if not paths:
            return
        cur = self.input.toPlainText().rstrip()
        block = quote_paths(paths)
        self.input.setPlainText((cur + "\n" if cur else "") + block + "\n")
        self.input.moveCursor(QTextCursor.End)
        self.input.setFocus()
        self._set_status(CANT["attached"].format(n=len(paths)))

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent
        if obj is self.view.viewport() and e.type() in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            paths = paths_from_mime(e.mimeData())
            if paths:
                e.acceptProposedAction()
                if e.type() == QEvent.Drop:
                    self.attach_paths(paths)
                return True
        return super().eventFilter(obj, e)

    def show_inbox_message(self, payload: dict) -> None:
        title = payload.get("title") or "Mikronous"
        self._add("inbox", f"{title}\n{payload.get('text', '')}")

    # ------------------------------------------------------------------ boot litany
    def play_litany(self) -> None:
        """Type out the awakening litany once per tray start (called by the app on first show)."""
        if self._litany_done:
            return
        self._litany_done = True
        lines = theme.litany_lines(_model_name(), "error" not in self.client.health())
        if not theme.litany_enabled():
            self._add("litany", "\n".join(lines))
            return
        self.messages.append({"role": "litany", "text": ""})
        entry = self.messages[-1]
        full = "\n".join(lines)
        step = {"i": 0}

        def tick() -> None:
            step["i"] = min(len(full), step["i"] + 2)
            entry["text"] = full[: step["i"]]
            self._render()
            if step["i"] < len(full):
                QTimer.singleShot(18, tick)
        tick()

    # ------------------------------------------------------------------ sending
    @Slot()
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self._worker is not None:
            return
        images, self._pending_images = self._pending_images, []
        self.input.clear()
        marker = f"[{len(images)} screen capture{'s' if len(images) > 1 else ''} attached]" if images else ""
        self._add("user", (marker + "\n" if marker else "") + text)
        self._streaming = ""
        self._schedule_render()
        self._start_worker((marker + " " if marker else "") + text, images)

    # ------------------------------------------------------------------ screen
    def capture_screen(self) -> None:
        """Hide the slate, let the user pick a region (Spectacle) or grab the screen, then ask."""
        from . import screen
        was_visible = self.isVisible()
        if was_visible:
            self.hide()
        QTimer.singleShot(350 if was_visible else 0, lambda: self._capture_now(was_visible))

    def _capture_now(self, reshow: bool) -> None:
        from . import screen
        screen.start_capture(self, True, self._capture_done)   # Spectacle runs as a QProcess; the tray stays responsive

    def _capture_done(self, path) -> None:
        self.show_window()
        if path is None:
            self._set_status(CANT["screen_cancelled"])
            return
        if not _vision_ready():                      # a text-only model would only get an error-text "description"
            self._add("system", f"++ CAPTURED {path.name}, BUT THE COGITATOR IS BLIND ++ load a model that sees: "
                                "mik model recommend --vision --apply   (then capture again)")
            self._set_status(CANT["screen_no_vision"])
            self._render()
            return
        self._pending_images.append(str(path))
        try:
            from PySide6.QtGui import QImage
            img = QImage(str(path))
            size = f" · {img.width()}×{img.height()}" if not img.isNull() else ""
        except Exception:  # noqa: BLE001
            size = ""
        self._set_status(CANT["screen_ready"].format(n=len(self._pending_images)) + size)
        if not self.input.toPlainText().strip():
            self.input.setPlainText("What's on my screen? ")
            self.input.moveCursor(QTextCursor.End)
        self.input.setFocus()

    def _start_worker(self, text: str, images: list[str] | None = None) -> None:
        self._detach_worker()                     # never two attached turns; an old one winds down ignored
        thread = QThread(self)
        worker = ChatWorker(self.client, text, self.session_id, images)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Signals cross threads through a guard QObject that lives on the GUI thread (queued delivery) and
        # drops everything from a turn that is no longer the current one.
        guard = _WorkerGuard(self, worker, thread)
        worker.delta.connect(guard.delta)
        worker.interim.connect(guard.interim)
        worker.tool_started.connect(guard.tool_started)
        worker.tool_completed.connect(guard.tool_completed)
        worker.approval.connect(guard.approval)
        worker.finished.connect(guard.finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(guard.thread_finished)
        self._thread, self._worker = thread, worker
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_dot("thinking")
        self._set_status(CANT["thinking"])
        thread.start()

    def _detach_worker(self) -> None:
        """Forget the running turn (its gateway run is asked to stop); it finishes in the background, ignored."""
        if self._worker is None:
            return
        self._worker.request_stop()
        self._zombies.append((self._thread, self._worker))
        self._thread = self._worker = None
        self._streaming = None
        self._copy_next_answer = False
        self._quiet_question = None
        self.approval_card.hide()
        self._pending_approval = None
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._stall_timer.stop()
        self._set_dot("idle")

    def live_threads(self) -> list[QThread]:
        """Threads the tray must wait for before quitting."""
        out = [t for t, _w in self._zombies] + [t for t in (self._thread, self._stt_thread, self._settings_thread) if t is not None]
        return [t for t in out if t.isRunning()]

    def stop_all(self) -> None:
        """Quit path: stop recording, cancel the turn, and give every thread a moment to end."""
        if self._recorder is not None:
            rec, self._recorder = self._recorder, None
            path = rec.stop()
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
        if self._speaker is not None:
            self._speaker.stop()
        for w in [self._worker] + [w for _t, w in self._zombies]:
            if w is not None:
                w.request_stop()
        for t in self.live_threads():
            t.quit()
            t.wait(1000)

    @Slot()
    def stop(self) -> None:
        if self._speaker is not None:
            self._speaker.stop()
        if self._worker:
            self._worker.request_stop()
            self._set_status(CANT["stopping"])

    def _cleanup_worker(self, thread: QThread, worker: "ChatWorker") -> None:
        thread.deleteLater()                      # the worker is Python-owned and goes with its last reference
        self._zombies = [(t, w) for t, w in self._zombies if w is not worker]
        if worker is not self._worker:
            return
        self._thread = None
        self._worker = None
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._set_dot("idle")
        self.input.setFocus()

    # ------------------------------------------------------------------ events
    @Slot(str)
    def _on_delta(self, delta: str) -> None:
        self._streaming = (self._streaming or "") + delta
        self._stall_timer.start()
        self._schedule_render()

    @Slot(str)
    def _on_interim(self, text: str) -> None:
        if text.strip():
            self._add("assistant", text.strip())

    @Slot(str, str)
    def _on_tool_started(self, tool: str, preview: str) -> None:
        self._set_status(f"++ RITE: {tool.upper()} ++")
        self._add("tool", f"RITE: {tool}" + (f" · {preview}" if preview else ""))

    @Slot(str, bool, str)
    def _on_tool_completed(self, tool: str, error: bool, preview: str) -> None:
        self._set_status(CANT["thinking"])
        if error:
            self._add("tool", f"RITE FAILED: {tool} · {preview[:200]}")

    @Slot(dict)
    def _on_approval(self, data: dict) -> None:
        self._pending_approval = data
        cmd = data.get("command") or data.get("tool_name") or "a tool call"
        desc = data.get("description") or "The machine spirit asks leave to run something that needs your sanction."
        self.approval_text.setText(
            f'<span style="font-family:\'{self.fonts["caps"]}\'; letter-spacing:2px; font-size:10px; color:{TOKENS["red"]};">SANCTION REQUIRED</span>'
            f'<br>{html.escape(str(desc))}<br><code style="font-family:\'{self.fonts["mono"]}\'; color:{TOKENS["fg"]};">{html.escape(str(cmd))}</code>')
        while self.approval_buttons.count():
            item = self.approval_buttons.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for choice in data.get("choices") or ["once", "deny"]:
            b = QPushButton(APPROVAL_LABELS.get(choice, choice).upper())
            if choice == "once":
                b.setObjectName("primary")
            b.clicked.connect(lambda _=False, c=choice: self._resolve_approval(c))
            self.approval_buttons.addWidget(b)
        self.approval_card.show()
        self._set_dot("approval")
        self._set_status(CANT["approval"])
        self._stall_timer.stop()                  # waiting on the user is not a stall
        self.show_window()

    def _resolve_approval(self, choice: str) -> None:
        data, self._pending_approval = self._pending_approval, None
        self.approval_card.hide()
        if not data or not self._worker or not self._worker.run_id:
            return
        try:
            self.client.approve(self._worker.run_id, choice, data.get("request_id"))
            self._add("tool", f"SANCTION: {APPROVAL_LABELS.get(choice, choice)}")
            self._set_dot("thinking")
            self._set_status(CANT["thinking"])
        except GatewayError as exc:
            self._add("system", f"Sanction could not be transmitted: {exc}")

    @Slot(str, str)
    def _on_finished(self, status: str, text: str) -> None:
        self.approval_card.hide()
        streamed = (self._streaming or "").strip()
        self._streaming = None
        quiet, self._quiet_question = self._quiet_question, None
        copy, self._copy_next_answer = self._copy_next_answer, False
        if status == "completed":
            final = text.strip() or streamed
            if final:
                self._add("assistant", final)
            self._set_status(CANT["complete"])
            if copy and final:
                selection.set_clipboard(final)
                self._set_status(CANT["copied"])
            if quiet is not None:
                self.answer_ready.emit(quiet, final or "(no answer)")
            if final:
                self.speak(final)
        elif status == "cancelled":
            if streamed:
                self._add("assistant", streamed)
            self._add("system", CANT["interrupted"])
            self._set_status("")
        else:
            if streamed:
                self._add("assistant", streamed)
            self._add("system", f"++ MALFUNCTION ++ {text.strip() or 'the rite ended without an answer (see `mikronous gateway status`)'}")
            self._set_status("")
            if quiet is not None:
                self.answer_ready.emit(quiet, text.strip() or "The rite failed; open the slate for details.")
        self._stall_timer.stop()
        self._render_timer.stop()
        self._render()                      # final state immediately, not on the next timer tick
        if self.pane.isVisible():
            self.pane.refresh()             # a first answer makes the session appear in the list

    def set_model_state(self, state: str) -> None:
        """Called by the tray: reflect the llama-server state when no turn is running."""
        self._model_state = state
        if self._worker is None and state in ("waking", "unloaded", "ready"):
            self._set_status(CANT[f"model_{state}"] if state != "ready" or self.status.text() else "")
            self._set_dot("thinking" if state == "waking" else "idle")

    # ------------------------------------------------------------------ misc
    def _set_status(self, text: str) -> None:
        self.status.setText(text)
        if self._pending_approval is not None:
            self._stall_timer.stop()
        elif self._worker is not None and text and text != CANT["stall"]:
            self._stall_timer.start()      # any progress resets the stall hint
        elif self._worker is None:
            self._stall_timer.stop()

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


class _WorkerGuard(QObject):
    """GUI-thread receiver for one ChatWorker: forwards its signals to the window only while that worker is
    the window's current turn, then frees itself when the thread ends."""

    def __init__(self, window: "ChatWindow", worker: ChatWorker, thread: QThread):
        super().__init__(window)
        self.window, self.worker, self.thread = window, worker, thread

    def _live(self) -> bool:
        return self.worker is self.window._worker

    @Slot(str)
    def delta(self, d: str) -> None:
        if self._live():
            self.window._on_delta(d)

    @Slot(str)
    def interim(self, t: str) -> None:
        if self._live():
            self.window._on_interim(t)

    @Slot(str, str)
    def tool_started(self, n: str, p: str) -> None:
        if self._live():
            self.window._on_tool_started(n, p)

    @Slot(str, bool, str)
    def tool_completed(self, n: str, e: bool, p: str) -> None:
        if self._live():
            self.window._on_tool_completed(n, e, p)

    @Slot(dict)
    def approval(self, d: dict) -> None:
        if self._live():
            self.window._on_approval(d)

    @Slot(str, str)
    def finished(self, s: str, t: str) -> None:
        if self._live():
            self.window._on_finished(s, t)

    @Slot()
    def thread_finished(self) -> None:
        self.window._cleanup_worker(self.thread, self.worker)
        self.deleteLater()


class _Transcribe(QObject):
    done = Signal(str, str)        # text, error

    def __init__(self, path: str, size: str):
        super().__init__()
        self.path, self.size = path, size

    @Slot()
    def run(self) -> None:
        from . import voice_io
        try:
            text = voice_io.transcribe(self.path, self.size)
            self.done.emit(text, "")
        except Exception as exc:  # noqa: BLE001
            self.done.emit("", f"{exc.__class__.__name__}: {str(exc)[:200]}")
        finally:
            try:
                os.remove(self.path)
            except OSError:
                pass


def _plain_text(md: str) -> str:
    """Markdown → something a voice can read: no code fences, bullets, links or cant sign-offs."""
    import re
    text = re.sub(r"```.*?```", " code omitted ", md, flags=re.S)
    text = re.sub(r"\+\+[^+\n]*\+\+", " ", text)                 # ++ 01001111 ++ style sign-offs
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)             # [text](url)
    text = re.sub(r"^[\s>*\-#]+", "", text, flags=re.M)              # bullets, quotes, headings
    text = re.sub(r"[`*_]{1,3}", "", text)
    return " ".join(text.split())


class _GatewayRestart(QObject):
    done = Signal(bool, str)

    @Slot()
    def run(self) -> None:
        ok, msg = prefs.restart_gateway()
        self.done.emit(ok, msg)


def _model_name() -> str:
    return prefs.model_name()


def _vision_ready() -> bool:
    """True when llama.env names a vision projector (set by `mik model use <vision preset>`)."""
    try:
        from mikronous_cli.paths import LLAMA_ENV, read_env
        return bool(read_env(LLAMA_ENV).get("LLAMA_MMPROJ", "").strip())
    except Exception:  # noqa: BLE001
        return False


def _cell(inner: str, *, bg: str, border: str, align: str = "left", indent_left: int = 0, indent_right: int = 0,
          margin_top: int = 8) -> str:
    """A framed block. Qt's rich text paints table-cell backgrounds and borders reliably; div backgrounds
    only cover the first line."""
    return (f'<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:{margin_top}px;"><tr>'
            + (f'<td width="{indent_left}"></td>' if indent_left else "")
            + f'<td bgcolor="{bg}" style="border:1px solid {border}; padding:6px 10px;" align="{align}">{inner}</td>'
            + (f'<td width="{indent_right}"></td>' if indent_right else "")
            + "</tr></table>")


def _render_message(m: dict) -> str:
    role, text = m.get("role"), m.get("text", "")
    t = TOKENS
    if role == "user":
        return _cell(_md(text), bg=t["user_bg"], border=t["red_line"], indent_left=56) \
            .replace(f'border:1px solid {t["red_line"]};', f'border:1px solid {t["red_line"]}; border-right:3px solid {t["red"]};')
    if role == "assistant":
        body = _md(text)
        prompt = '<span class="prompt">&gt; </span>'
        if "<p" in body:
            i = body.index(">", body.index("<p")) + 1
            body = body[:i] + prompt + body[i:]
        else:
            body = prompt + body
        return f'<div class="ai">{body}</div>'
    if role == "inbox":
        title, _, body = text.partition("\n")
        return _cell(f'<span class="label">{html.escape(title.upper())}</span><br>{_md(body)}', bg=t["bg2"], border=t["brass_dim"])
    if role == "tool":
        return f'<div class="chip">++ {html.escape(text)} ++</div>'
    if role == "litany":
        return '<div class="litany">' + "<br>".join(html.escape(line) for line in text.split("\n")) + "</div>"
    return f'<div class="system">{html.escape(text)}</div>'


def _md(text: str) -> str:
    """Markdown → HTML via Qt's own converter (no extra dependency)."""
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setMarkdown(text)
    body = doc.toHtml()
    start, end = body.find("<body"), body.rfind("</body>")
    if start != -1 and end != -1:
        body = body[body.find(">", start) + 1:end]
    return body
