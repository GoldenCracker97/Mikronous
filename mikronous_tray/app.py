"""Tray icon, menu, single-instance control socket, and wiring between window, inbox and gateway."""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

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


class UpdateChecker(QThread):
    """`mik update --check` on a thread: fetches origin and reports {behind, commits, remote, error}."""
    result = Signal(dict)

    def run(self) -> None:
        from mikronous_cli import update
        try:
            err = update.repo_ok()
            if err:
                self.result.emit({"error": err})
                return
            self.result.emit(update.status())
        except Exception as exc:  # noqa: BLE001 - network or git trouble is reported, never raised
            self.result.emit({"error": f"{exc.__class__.__name__}: {str(exc).strip()[-200:]}"})


UPDATE_FIRST_CHECK_MS = 20_000
UPDATE_RECHECK_MS = 6 * 3600 * 1000


class TrayApp(QObject):
    krunner_run = Signal(str)            # match id from the D-Bus thread → handled on the Qt thread

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
        a = QAction("Past chats", menu)
        a.triggered.connect(lambda: (self.window.show_window(), self.window.toggle_sidebar(True)))
        menu.addAction(a)
        a = QAction("Settings…", menu)
        a.triggered.connect(lambda: (self.window.show_window(), self.window.open_settings()))
        menu.addAction(a)
        a = QAction("Routines…", menu)
        a.triggered.connect(lambda: (self.window.show_window(), self.window.open_settings("routines")))
        menu.addAction(a)
        a = QAction("Open notes folder", menu)
        a.triggered.connect(self._open_notes)
        menu.addAction(a)
        a = QAction("Gateway status", menu)
        a.triggered.connect(self._status)
        menu.addAction(a)
        a = QAction("Update Mikronous…", menu)
        a.triggered.connect(lambda: self.check_updates(interactive=True))
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
        self.tray.messageClicked.connect(self.window.show_window)
        self.tray.show()
        self._orig_show = self.window.show_window
        self.window.show_window = self._show_with_litany  # first show after start plays the litany
        self.window.update_requested.connect(lambda: self.check_updates(interactive=True))
        self.window.hotkey_changed.connect(self._rebind_hotkey)
        self._checker: UpdateChecker | None = None
        self._update_interactive = False
        self._update_status: dict = {}
        QTimer.singleShot(UPDATE_FIRST_CHECK_MS, lambda: self.check_updates(interactive=False))
        self._recheck = QTimer(self, interval=UPDATE_RECHECK_MS)
        self._recheck.timeout.connect(lambda: self.check_updates(interactive=False))
        self._recheck.start()

        # Windows: register the global hotkey ourselves (KDE does it through kglobalshortcutsrc).
        self.hotkey = None
        self.hotkey_selection = None
        self.hotkey_vox = None
        if sys.platform == "win32":
            self._register_hotkeys()

        # KRunner (Linux): `mik <question>` in Alt+Space; answers arrive as notifications when the window is hidden.
        self.krunner = None
        self.window.answer_ready.connect(self._quiet_answer)
        if sys.platform.startswith("linux") and os.environ.get("MIKRONOUS_NO_KRUNNER", "") not in ("1", "true", "yes"):
            from .krunner import KRunnerService
            self.krunner = KRunnerService(self.krunner_run.emit)
            self.krunner_run.connect(self._krunner_run)
            if not self.krunner.start():
                print(f"mikronous-tray: KRunner integration off: {self.krunner.error}", file=sys.stderr)

        # The model is loaded while the tray runs and unloaded when it quits.
        self.model = ModelService(self)
        self.model.state_changed.connect(self._model_state)
        self.model.ensure_loaded()
        self._model_state(self.model.state)

    def _register_hotkeys(self) -> None:
        from .winhotkey import HOTKEY_ID, WinHotkey
        st = settings.load()
        self.hotkey = WinHotkey(st.get("hotkey", "Ctrl+Alt+Space"), self.window.toggle, self.app, HOTKEY_ID)
        self.hotkey_selection = WinHotkey(st.get("hotkey_selection", "Ctrl+Alt+Shift+Space"), self.window.selection_menu,
                                          self.app, HOTKEY_ID + 1)
        self.hotkey_vox = WinHotkey(st.get("hotkey_vox", "Ctrl+Alt+V"), self.window.vox_toggle, self.app, HOTKEY_ID + 2)
        for hk in (self.hotkey, self.hotkey_selection, self.hotkey_vox):
            if not hk.ok:
                print(f"mikronous-tray: could not register hotkey {hk.spec}: {hk.error}", file=sys.stderr)

    def _rebind_hotkey(self, _spec: str) -> None:
        if sys.platform != "win32":
            return
        for hk in (self.hotkey, self.hotkey_selection, self.hotkey_vox):
            if hk:
                hk.unregister()
        self._register_hotkeys()
        for hk in (self.hotkey, self.hotkey_selection, self.hotkey_vox):
            if hk and not hk.ok:
                self.tray.showMessage("Mikronous", f"Could not register hotkey {hk.spec}: {hk.error}", QSystemTrayIcon.Warning, 6000)

    # ------------------------------------------------------------------ updates
    def check_updates(self, *, interactive: bool) -> None:
        """Fetch origin on a thread. Interactive (button/menu): report either way and offer to apply.
        Background: only mark the UPDATE button when something new is on GitHub."""
        if self._checker is not None and self._checker.isRunning():
            self._update_interactive = self._update_interactive or interactive
            return
        if not interactive and self._update_status.get("behind"):
            return                                              # already flagged; nothing to re-fetch
        self._update_interactive = interactive
        if interactive:
            self.window.set_update_state("checking")
        self._checker = UpdateChecker(self)
        self._checker.result.connect(self._update_checked)
        self._checker.start()

    def _update_checked(self, st: dict) -> None:
        self._update_status = st
        interactive, self._update_interactive = self._update_interactive, False
        if "error" in st:
            if interactive:
                self.window.set_update_state("failed", st["error"])
            return
        behind = int(st.get("behind") or 0)
        if not behind:
            if interactive:
                self.window.set_update_state("current", f"Mikronous is at {st.get('local', '?')} on {st.get('branch', '?')}")
            else:
                self.window.set_update_state("idle")
            return
        summary = f"{behind} new commit(s)"
        self.window.set_update_state("available", summary)
        if st.get("dirty") or st.get("ahead"):
            if interactive:
                QMessageBox.information(self.window, "Mikronous update",
                                        "There are new commits on GitHub, but this checkout has local changes or commits.\n"
                                        "Run `mik update` in a terminal to see what blocks the fast-forward.")
            return
        if interactive:
            self._offer_update(st)

    def _offer_update(self, st: dict) -> None:
        lines = "\n".join(f"  {c}" for c in (st.get("commits") or [])[:8])
        more = "" if len(st.get("commits") or []) <= 8 else f"\n  … and {len(st['commits']) - 8} more"
        box = QMessageBox(self.window)
        box.setWindowTitle("Mikronous update")
        box.setText(f"{st['behind']} new commit(s) on GitHub:\n{lines}{more}\n\nPull, re-install and restart the tray now?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Later)
        box.setDefaultButton(QMessageBox.Yes)
        box.setStyleSheet(self.window.styleSheet())
        self.window.show_window()
        if box.exec() == QMessageBox.Yes:
            self._update()

    # ------------------------------------------------------------------ KRunner
    def _krunner_run(self, match_id: str) -> None:
        kind, _, arg = match_id.partition(":")
        if kind == "ask":
            self.window.ask_quietly(arg)
        elif kind == "cmd":
            self._control_command(arg)

    def _quiet_answer(self, question: str, answer: str) -> None:
        if self.window.isVisible():
            return
        short = answer.strip().replace("\n\n", "\n")
        self.tray.showMessage(f"Mikronous: {question[:60]}", short[:400] + ("…" if len(short) > 400 else ""),
                              QSystemTrayIcon.Information, 15000)

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
            self._control_command(cmd)

    def _control_command(self, cmd: str) -> None:
        if cmd == "toggle":
            self.window.toggle()
        elif cmd == "show":
            self.window.show_window()
        elif cmd == "hide":
            self.window.hide_window()
        elif cmd == "new":
            self.window.new_chat()
            self.window.show_window()
        elif cmd == "chats":
            self.window.show_window()
            self.window.toggle_sidebar()
        elif cmd == "settings":
            self.window.show_window()
            self.window.open_settings()
        elif cmd == "routines":
            self.window.show_window()
            self.window.open_settings("routines")
        elif cmd == "selection":
            self.window.selection_menu()
        elif cmd == "vox":
            self.window.vox_toggle()
        elif cmd == "update":
            self.window.show_window()
            self.check_updates(interactive=True)
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
        if sys.platform == "win32":
            os.startfile(str(d))  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _update(self) -> None:
        """Run `mik update` detached; it pulls, re-installs and restarts this tray by itself."""
        from mikronous_cli.platform import IS_WINDOWS, conf_dir
        log = conf_dir() / "update.log"
        self.window.set_update_state("running")
        self.tray.showMessage("Mikronous", f"Updating… the tray restarts by itself (log: {log})", QSystemTrayIcon.Information, 4000)
        kwargs: dict = {"stdin": subprocess.DEVNULL}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        out = open(log, "ab")  # noqa: SIM115 - handed to the child
        subprocess.Popen([sys.executable, "-m", "mikronous_cli", "update"], stdout=out, stderr=subprocess.STDOUT, **kwargs)

    def _status(self) -> None:
        h = self.client.health()
        text = f"Gateway error: {h['error']}" if "error" in h else f"Gateway OK at {self.client.v1}"
        self.tray.showMessage("Mikronous", text, QSystemTrayIcon.Information, 4000)

    def quit(self) -> None:
        for hk in (self.hotkey, self.hotkey_selection, self.hotkey_vox):
            if hk:
                hk.unregister()
        if self.krunner:
            self.krunner.stop()
        self.window.stop()
        if self.model.unload_on_quit():
            self.model.unload()          # frees the VRAM; MIKRONOUS_KEEP_MODEL=1 keeps it resident
        self.inbox.stop()
        self.control.close()
        QLocalServer.removeServer(settings.CONTROL_SERVER)
        self.client.close()
        self.app.quit()
