"""Tray window tests on Qt's offscreen platform (skipped when PySide6 is not installed)."""
import json
import os
import socket
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mikronous_tray.hermes_client import RunEvent  # noqa: E402


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rt")
    os.environ["XDG_RUNTIME_DIR"] = str(tmp)
    os.environ["HOME"] = str(tmp / "home")
    os.makedirs(os.environ["HOME"], exist_ok=True)
    os.environ["MIKRONOUS_NO_LITANY"] = "1"
    return QApplication.instance() or QApplication([])


def pump(app, cond, timeout=10):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(0.02)
    return cond()


class FakeClient:
    v1 = "http://fake/v1"

    def __init__(self):
        self.approved, self.stopped = [], False

    def session_messages(self, sid, limit=200):
        return [{"role": "user", "content": "earlier q"}, {"role": "assistant", "content": "earlier a"}]

    def start_run(self, text, sid):
        return "run1"

    def events(self, run_id, should_stop=None):
        yield RunEvent("message.delta", {"delta": "Hi "})
        yield RunEvent("tool.started", {"tool": "set_reminder", "preview": "{...}"})
        yield RunEvent("tool.completed", {"tool": "set_reminder", "error": False})
        yield RunEvent("approval.request", {"command": "rm -rf x", "description": "dangerous",
                                            "choices": ["once", "deny"], "request_id": "a1"})
        for _ in range(200):
            if self.approved:
                break
            time.sleep(0.05)
        yield RunEvent("run.completed", {"output": "**Done**: reminder set."})

    def approve(self, run_id, choice, request_id=None):
        self.approved.append((choice, request_id))

    def stop(self, run_id):
        self.stopped = True

    def health(self):
        return {"data": []}

    def list_sessions(self, limit=100):
        return [{"id": "tray-20261003-aaaa", "title": "Reminder rites", "last_active": time.time() - 60, "message_count": 6},
                {"id": "api-ask-1", "preview": "mik ask session"},
                {"id": "tray-20260901-bbbb", "preview": "What devices are on my network?\nand more", "started_at": "2026-09-01T10:00:00"}]

    def rename_session(self, sid, title):
        self.renamed = (sid, title)

    def delete_session(self, sid):
        self.deleted = sid

    def close(self):
        pass


def test_theme_assets(app):
    from PySide6.QtGui import QIcon
    from mikronous_tray import theme
    fam = theme.load_fonts()
    assert fam == {"caps": "Cinzel", "mono": "Share Tech Mono", "gothic": "Grenze Gotisch"}
    for kind in ("color", "symbolic-light", "symbolic-dark"):
        assert not QIcon(str(theme.icon_path(kind))).isNull()
    assert [s.width() for s in theme.qicon("color").availableSizes()] == [16, 22, 24, 32, 48, 64, 128, 256]


def test_window_turn_with_approval(app):
    from mikronous_tray import settings
    from mikronous_tray.chat_window import ChatWindow
    settings.save(session_id="tray-test")
    fc = FakeClient()
    w = ChatWindow(fc)
    assert "earlier q" in w.view.toPlainText()
    w.input.setPlainText("Remind me")
    w.send()
    assert pump(app, lambda: w.approval_card.isVisible())
    assert "rm -rf x" in w.approval_text.text()
    btn = w.approval_buttons.itemAt(0).widget()
    assert btn.text() == "SANCTION ONCE"
    btn.click()
    assert pump(app, lambda: w._worker is None, 15)
    app.processEvents()
    w._render()
    txt = w.view.toPlainText()
    assert fc.approved == [("once", "a1")]
    for needle in ("Done: reminder set.", "RITE: set_reminder", "Remind me", "SANCTION: Sanction once"):
        assert needle in txt
    w.play_litany()
    w._render()
    assert "MACHINE GOD PROVIDES" in w.view.toPlainText()
    w.play_litany()
    assert sum(1 for m in w.messages if m["role"] == "litany") == 1


def test_inbox_socket(app):
    import sys
    from mikronous_tray import settings
    from mikronous_tray.inbox import InboxServer
    from mikronous_cli.platform import INBOX_NAME, local_server_path
    got = []
    ib = InboxServer()
    assert ib.start()
    ib.message.connect(got.append)
    line = (json.dumps({"ts": 1, "chat_id": "desktop", "text": "Stretch", "title": "Reminder"}) + "\n").encode()
    if sys.platform == "win32":
        import threading

        def _write():   # the pipe write blocks until the tray reads, so pump the loop while a thread writes
            with open(local_server_path(INBOX_NAME), "r+b", buffering=0) as pipe:   # same path the plugin uses
                pipe.write(line)
        threading.Thread(target=_write, daemon=True).start()
    else:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(settings.socket_path()))
        s.sendall(line)
        s.close()
    assert pump(app, lambda: got, 5)
    assert got[0]["text"] == "Stretch"
    ib.stop()


def test_model_service_without_systemd(app, monkeypatch):
    from mikronous_tray import model_service
    from mikronous_model import runner
    monkeypatch.setattr(runner, "available", lambda: False)
    monkeypatch.setattr(runner, "is_running", lambda: False)
    monkeypatch.setattr(model_service.ModelService, "answers", staticmethod(lambda: False))
    svc = model_service.ModelService()
    assert svc.refresh() == "unknown"
    svc.ensure_loaded()                      # must not raise without systemd
    svc.unload()
    assert svc.state == "unloaded"
    monkeypatch.setenv("MIKRONOUS_KEEP_MODEL", "1")
    assert svc.unload_on_quit() is False


def test_model_service_systemd_flow(app, monkeypatch):
    from mikronous_tray import model_service
    from mikronous_model import runner
    calls = []
    state = {"active": False, "answers": False}

    def fake(action):
        def f():
            calls.append((action, model_service.UNIT))
            if action == "start":
                state["active"] = True
            if action == "stop":
                state["active"] = False
            return True
        return f
    monkeypatch.setattr(runner, "available", lambda: True)
    monkeypatch.setattr(runner, "is_running", lambda: state["active"])
    monkeypatch.setattr(runner, "start", fake("start"))
    monkeypatch.setattr(runner, "stop", fake("stop"))
    monkeypatch.setattr(model_service.ModelService, "answers", staticmethod(lambda: state["answers"]))
    seen = []
    svc = model_service.ModelService()
    svc.state_changed.connect(seen.append)
    svc.ensure_loaded()
    assert ("start", model_service.UNIT) in calls and svc.state == "waking"
    state["answers"] = True
    assert pump(app, lambda: svc.state == "ready", 5)
    svc.unload()
    assert ("stop", model_service.UNIT) in calls and svc.state == "unloaded"
    assert seen[:1] == ["waking"] or "waking" in seen


def test_session_labels():
    from mikronous_tray.sessions_pane import session_label
    now = time.mktime((2026, 10, 3, 15, 0, 0, 0, 0, -1))
    title, second = session_label({"title": "Hello", "last_active": now - 120, "message_count": 4}, now)
    assert title == "Hello" and second == "today 14:58 · 4 msgs"
    title, second = session_label({"preview": "x" * 80, "started_at": "2026-09-01T10:00:00"}, now)
    assert title.endswith("…") and len(title) == 46 and second == "2026-09-01"
    assert session_label({}, now) == ("(empty rite)", "")


def test_sessions_pane_and_open(app):
    from mikronous_tray import settings
    from mikronous_tray.chat_window import ChatWindow
    settings.save(session_id="tray-current", sidebar=False)
    fc = FakeClient()
    w = ChatWindow(fc)
    w.resize(520, 600)
    w.show()
    assert not w.pane.isVisible()
    w.toggle_sidebar()
    assert w.pane.isVisible() and w.btn_chats.isChecked() and w.width() == 740
    assert pump(app, lambda: w.pane.list.count() == 2, 5)
    assert w.pane.session_ids() == ["tray-20261003-aaaa", "tray-20260901-bbbb"]      # mik ask sessions are hidden
    assert w.pane.list.item(0).text().startswith("Reminder rites\ntoday") or "msgs" in w.pane.list.item(0).text()
    w.pane._clicked(w.pane.list.item(1))
    assert w.session_id == "tray-20260901-bbbb" and settings.load()["session_id"] == w.session_id
    assert "earlier q" in w.view.toPlainText() and w.pane.list.currentRow() == 1
    from mikronous_tray import sessions_pane
    monkey = sessions_pane.QInputDialog.getText
    sessions_pane.QInputDialog.getText = staticmethod(lambda *a, **k: ("Network rite", True))   # no modal dialog offscreen
    try:
        w.pane.rename("tray-20260901-bbbb", "What devices…")
    finally:
        sessions_pane.QInputDialog.getText = monkey
    assert fc.renamed == ("tray-20260901-bbbb", "Network rite")
    w.toggle_sidebar()
    assert not w.pane.isVisible() and w.width() == 520 and settings.load()["sidebar"] is False
    w.hide()


def test_update_button_states(app):
    from mikronous_tray.chat_window import ChatWindow
    w = ChatWindow(FakeClient())
    got = []
    w.update_requested.connect(lambda: got.append(1))
    w.btn_update.click()
    assert got == [1]
    w.set_update_state("checking")
    assert not w.btn_update.isEnabled() and "FORGE" in w.status.text()
    w.set_update_state("available", "2 new commit(s)")
    assert w.btn_update.text() == "UPDATE •" and w.btn_update.property("alert") == "true" and "2 new commit(s)" in w.status.text()
    w.set_update_state("current")
    assert w.btn_update.text() == "UPDATE" and w.btn_update.isEnabled() and "LATEST" in w.status.text()


def test_settings_dialog_values(app):
    from mikronous_tray import prefs
    from mikronous_tray.settings_dialog import SettingsDialog
    cur = prefs.Prefs(voice="full", approvals="smart", internet=True, notes_dir="~/N", docs_dirs="~/D")
    d = SettingsDialog(None, cur, model="Qwen3-8B-Q4_K_M", kde_shortcut="Meta+Space")
    assert d.values() == cur
    d.voice.setCurrentIndex(0)
    d.approvals.setCurrentIndex(1)
    d.internet.setChecked(False)
    d.keep_model.setChecked(True)
    d.docs_dirs.setText("~/Docs")
    v = d.values()
    assert (v.voice, v.approvals, v.internet, v.keep_model, v.docs_dirs) == ("plain", "manual", False, True, "~/Docs")
    assert v.changed_from(cur) == ["voice", "approvals", "internet", "keep_model", "docs_dirs"]


def test_routines_helpers():
    from mikronous_tray import routines as R
    now = 1_000_000.0
    job = {"id": "j1", "name": "Morning briefing", "deliver": "mikronous", "schedule_display": "every 1d at 08:00",
           "next_run_at": now + 9 * 3600 + 120, "enabled": True, "state": "scheduled", "last_status": "success"}
    assert R.is_routine(job) and not R.is_reminder(job) and not R.paused(job)
    assert R.summary(job, now) == ("Morning briefing", "every 1d at 08:00 · in 9h 02m · last: success · routine")
    rem = {"name": "Reminder: stretch", "deliver": "mikronous", "no_agent": True, "schedule": {"kind": "once", "display": "once"},
           "next_run_at": now + 30, "enabled": False, "state": "paused"}
    assert R.is_reminder(rem) and R.paused(rem) and R.summary(rem, now)[1] == "once · PAUSED · reminder"
    assert not R.is_routine({"deliver": "local"}) and R.is_routine({"deliver": "mikronous:desktop"})
    assert R.when_text(now - 400, now) == "overdue" and R.when_text(now + 3 * 86400, now) == "in 3d" and R.when_text(None) == ""
    assert R.preset("briefing").skills == ("daily-briefing",) and R.preset("nope").key == "custom"


def test_routines_tab(app):
    from mikronous_tray import routines_tab
    from mikronous_tray.routines_tab import RoutineDialog, RoutinesTab
    calls = []

    class JobsClient:
        def list_jobs(self, include_disabled=True):
            return [{"id": "j1", "name": "Morning briefing", "deliver": "mikronous", "schedule_display": "every 1d at 08:00",
                     "next_run_at": time.time() + 3600, "enabled": True, "state": "scheduled", "prompt": "Run it"},
                    {"id": "j2", "name": "Reminder: blink", "deliver": "mikronous", "no_agent": True, "schedule_display": "once",
                     "enabled": True, "state": "scheduled"},
                    {"id": "j3", "name": "telegram thing", "deliver": "telegram"}]

        def create_job(self, name, schedule, prompt, deliver="mikronous", skills=None):
            calls.append(("create", name, schedule, deliver, tuple(skills or ()))); return {"id": "new"}

        def update_job(self, job_id, **fields):
            calls.append(("update", job_id, fields)); return {}

        def delete_job(self, job_id):
            calls.append(("delete", job_id))

        def job_action(self, job_id, action):
            calls.append((action, job_id)); return {}

    tab = RoutinesTab(JobsClient())
    tab.refresh()
    assert pump(app, lambda: tab.list.count() == 2, 5)
    assert [j["id"] for j in tab.jobs] == ["j1", "j2"]
    tab.list.setCurrentRow(1)
    assert not tab.btn_edit.isEnabled() and tab.btn_pause.text() == "PAUSE"
    tab.list.setCurrentRow(0)
    assert tab.btn_edit.isEnabled()
    tab.toggle_pause(); tab.run_now()
    assert ("pause", "j1") in calls and ("run", "j1") in calls
    dlg = RoutineDialog(None, None, "briefing")
    v = dlg.values()
    assert v["name"] == "Morning briefing" and v["skills"] == ["daily-briefing"] and "daily-briefing" in v["prompt"]
    dlg.preset.setCurrentIndex([p.key for p in routines_tab.R.PRESETS].index("custom"))
    assert dlg.values()["name"] == "" and dlg.values()["prompt"] == ""
    edit = RoutineDialog(None, {"name": "X", "schedule_display": "every 6h", "prompt": "p", "skills": ["s"]})
    assert edit.values() == {"name": "X", "schedule": "every 6h", "prompt": "p", "skills": ["s"]}
    # NEW… through a stubbed dialog exec
    monkey = routines_tab.RoutineDialog.exec
    routines_tab.RoutineDialog.exec = lambda self: (self.preset.setCurrentIndex(0), 1)[1]
    try:
        tab.new()
    finally:
        routines_tab.RoutineDialog.exec = monkey
    assert ("create", "Morning briefing", "every 1d at 08:00", "mikronous", ("daily-briefing",)) in calls
