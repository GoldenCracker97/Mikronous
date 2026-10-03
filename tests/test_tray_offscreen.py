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
        with open(local_server_path(INBOX_NAME), "r+b", buffering=0) as pipe:   # same path the plugin uses
            pipe.write(line)
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
