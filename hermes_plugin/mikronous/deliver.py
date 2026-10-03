"""Deliver an outbound message to the desktop: notification + durable inbox + live tray socket.

Shared by the gateway adapter (in-process) and the standalone sender (cron running on its own).
The inbox file guarantees nothing is lost when the tray app is not running.
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from . import desktop

DATA_DIR = Path(os.environ.get("MIKRONOUS_DATA_DIR", "~/.local/share/mikronous")).expanduser()
INBOX = DATA_DIR / "inbox.jsonl"
MAX_NOTIFY_CHARS = 600


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "mikronous.sock"


def _split_title(text: str) -> tuple[str, str]:
    """First short line becomes the title when the message has several lines."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) >= 2 and len(lines[0]) <= 60:
        return lines[0].lstrip("#* ").strip(), "\n".join(lines[1:])
    return "Mikronous", text.strip()


def _push_to_tray(payload: dict) -> bool:
    path = socket_path()
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
    payload = {"ts": time.time(), "chat_id": chat_id or "desktop", "text": text, "source": source}
    try:
        INBOX.parent.mkdir(parents=True, exist_ok=True)
        with INBOX.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        payload["inbox"] = True
    except OSError as exc:
        payload["inbox"] = False
        payload["inbox_error"] = str(exc)
    payload["tray"] = _push_to_tray(payload)
    title, body = _split_title(text)
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
