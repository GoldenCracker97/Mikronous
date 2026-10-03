"""Deliver an outbound message to the desktop: notification + durable inbox + live tray socket.

Shared by the gateway adapter (in-process) and the standalone sender (cron running on its own).
The inbox file guarantees nothing is lost when the tray app is not running.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

from . import desktop
from ._paths import data_dir

IS_WINDOWS = sys.platform == "win32"
DATA_DIR = data_dir()
INBOX = DATA_DIR / "inbox.jsonl"
MAX_NOTIFY_CHARS = 600
PIPE_NAME = "mikronous-inbox"


def socket_path() -> Path:
    """Where the tray listens: a Unix socket on Linux, a named pipe on Windows (QLocalServer on both ends)."""
    if IS_WINDOWS:
        return Path(rf"\\.\pipe\{PIPE_NAME}")
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "mikronous.sock"


# Hermes cron wraps no-agent output as "Cronjob Response: <name>\n(job_id: …)\n-------------\n\n<body>\n\n
# To stop or manage this job, send me a new message (…)". Mirrors cron/scheduler_delivery.py; the
# footer makes no sense on a desktop popup and the header is a better title than a first line.
CRON_HEADER = "Cronjob Response: "
CRON_DIVIDER = "\n-------------\n"
CRON_FOOTER = "\n\nTo stop or manage this job, send me a new message"


def _strip_cron_wrapper(text: str) -> tuple[str | None, str]:
    """Return (title, body) for a wrapped cron delivery, (None, text) for anything else."""
    if not text.startswith(CRON_HEADER) or CRON_DIVIDER not in text:
        return None, text
    head, _, body = text.partition(CRON_DIVIDER)
    name = head[len(CRON_HEADER):].splitlines()[0].strip()
    if CRON_FOOTER in body:
        body = body[: body.index(CRON_FOOTER)]
    body = body.strip()
    title = "Reminder" if name.lower().startswith("reminder") else (name or "Mikronous")
    return title, body or name


def _split_title(text: str) -> tuple[str, str]:
    """First short line becomes the title when the message has several lines."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) >= 2 and len(lines[0]) <= 60:
        return lines[0].lstrip("#* ").strip(), "\n".join(lines[1:])
    return "Mikronous", text.strip()


def _push_to_tray(payload: dict) -> bool:
    path = socket_path()
    if IS_WINDOWS:
        # Qt's QLocalServer pipe has no buffer: a write blocks until the tray reads it. Do it on a
        # helper thread with a deadline so a stuck tray can never hang reminder delivery.
        import threading
        done = {"ok": False}

        def _write() -> None:
            try:
                with open(str(path), "r+b", buffering=0) as pipe:
                    pipe.write((json.dumps(payload) + "\n").encode("utf-8"))
                done["ok"] = True
            except OSError:
                done["ok"] = False   # tray not running: the inbox file and the toast still deliver
        t = threading.Thread(target=_write, daemon=True)
        t.start()
        t.join(2.0)
        return done["ok"]
    if not path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(str(path))
            s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        return True
    except OSError:
        return False


def deliver(chat_id: str, text: str, *, source: str = "gateway") -> dict:
    text = (text or "").strip()
    if not text:
        return {"error": "empty message"}
    cron_title, text = _strip_cron_wrapper(text)
    payload = {"ts": time.time(), "chat_id": chat_id or "desktop", "text": text, "source": source}
    if cron_title:
        payload["title"] = cron_title
    try:
        INBOX.parent.mkdir(parents=True, exist_ok=True)
        with INBOX.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        payload["inbox"] = True
    except OSError as exc:
        payload["inbox"] = False
        payload["inbox_error"] = str(exc)
    payload["tray"] = _push_to_tray(payload)
    title, body = (cron_title, text) if cron_title else _split_title(text)
    if len(body) > MAX_NOTIFY_CHARS:
        body = body[:MAX_NOTIFY_CHARS - 1] + "…"
    note = desktop.notify(title, body, urgency="normal")
    payload["notified"] = bool(note.get("ok"))
    if not payload["notified"]:
        payload["notify_error"] = note.get("error")
    ok = payload["notified"] or payload["tray"] or payload["inbox"]
    msg_id = f"mik-{int(payload['ts'] * 1000)}"
    return {"success": ok, "message_id": msg_id, **{k: v for k, v in payload.items() if k not in ("text",)}} if ok \
        else {"error": f"no delivery path worked: {payload.get('notify_error')}; {payload.get('inbox_error')}"}
