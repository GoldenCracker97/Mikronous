"""Mikronous client tools, registered from ``register()`` in ``__init__`` via ``register_tools(ctx)``.

Handlers return dicts (``mik docs``, ``/notes`` and the tests call them directly); Hermes's tool
contract only accepts strings, so ``register_tools`` wraps each handler to JSON-encode its result.
The ``/notes`` slash command and the hooks are registered here too.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from . import desktop, docs_index, network, notes, reminders

logger = logging.getLogger(__name__)
TOOLSET = "mikronous"
PLUGIN_DIR = Path(__file__).resolve().parent
REPO_DIR = PLUGIN_DIR.parent.parent  # <repo>/hermes_plugin/mikronous -> <repo>
SKILLS_DIR = REPO_DIR / "skills"


def _err(msg: str) -> dict:
    return {"error": msg}


# ----------------------------------------------------------------------------- handlers
def desktop_notify(args: dict, **_: Any) -> dict:
    return desktop.notify(str(args.get("title") or "Mikronous"), str(args.get("body") or ""),
                          str(args.get("urgency") or "normal"))


def desktop_open(args: dict, **_: Any) -> dict:
    kind = str(args.get("kind") or "auto")
    if kind not in ("auto", "url", "file", "app"):
        return _err("kind must be auto, url, file or app")
    return desktop.open_target(str(args.get("target") or ""), kind)


def clipboard(args: dict, **_: Any) -> dict:
    action = str(args.get("action") or "get")
    if action == "get":
        return desktop.clipboard_get()
    if action == "set":
        if "text" not in args:
            return _err("set needs 'text'")
        return desktop.clipboard_set(str(args.get("text") or ""))
    return _err("action must be get or set")


def notes_manage(args: dict, **_: Any) -> dict:
    action = str(args.get("action") or "list")
    try:
        if action == "add":
            if not (args.get("title") or args.get("body")):
                return _err("add needs a title or body")
            tags = args.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            n = notes.add(str(args.get("title") or ""), str(args.get("body") or ""), list(tags), str(args.get("due") or ""))
            return {"ok": True, "note": n.to_dict()}
        if action == "list":
            status = str(args.get("status") or "open")
            items = notes.list_notes(status, str(args.get("tag") or ""))
            return {"count": len(items), "status": status,
                    "notes": [{k: v for k, v in n.to_dict().items() if k != "path"} for n in items[: int(args.get("limit") or 50)]]}
        if action == "search":
            hits = notes.search(str(args.get("query") or ""), int(args.get("limit") or 10))
            return {"count": len(hits), "notes": [{**n.to_dict(), "score": round(-s, 3)} for n, s in hits]}
        if action == "get":
            n = notes.get(str(args.get("id") or ""))
            return {"note": n.to_dict()} if n else _err("no such note")
        if action in ("done", "undone", "update", "delete"):
            n = notes.get(str(args.get("id") or ""))
            if not n:
                return _err("no such note (use list/search to find the id)")
            if action == "delete":
                notes.delete(n.id)
                return {"ok": True, "deleted": n.id}
            if action in ("done", "undone"):
                n.done = action == "done"
            else:
                if "title" in args:
                    n.title = str(args["title"]).strip()[:120] or n.title
                if "body" in args:
                    n.body = str(args["body"]).strip()
                if "append" in args:
                    n.body = (n.body + "\n" + str(args["append"]).strip()).strip()
                if "tags" in args:
                    tags = args["tags"]
                    n.tags = [t.strip() for t in (tags.split(",") if isinstance(tags, str) else tags) if t.strip()]
                if "due" in args:
                    n.due = str(args["due"] or "").strip()
            notes.save(n)
            return {"ok": True, "note": n.to_dict()}
        return _err("action must be add, list, search, get, done, undone, update or delete")
    except OSError as exc:
        return _err(f"notes storage error: {exc}")


def docs_search(args: dict, **_: Any) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return _err("query is required")
    if args.get("reindex"):
        st = docs_index.reindex(force=False)
        logger.info("docs reindex: %s", st)
    try:
        hits = docs_index.search(query, int(args.get("limit") or 8))
    except Exception as exc:  # noqa: BLE001
        return _err(f"document index error: {exc}")
    info = docs_index.stats()
    return {"count": len(hits), "results": hits, "indexed_files": info["files"], "dirs": info["dirs"],
            "next_step": "Call read_file on the most relevant path (use offset/limit for long files) before answering."}


# ----------------------------------------------------------------------------- schemas
def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    params: dict = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    return {"name": name, "description": description, "parameters": params}


SCHEMAS = {
    "desktop_notify": _schema(
        "desktop_notify",
        "Show a desktop notification on the user's KDE desktop. Use for reminders, finished background work, or anything the user should notice even when the chat window is closed.",
        {"title": {"type": "string", "description": "Short heading (<= 60 chars)"},
         "body": {"type": "string", "description": "One or two lines of text"},
         "urgency": {"type": "string", "enum": ["low", "normal", "critical"], "description": "critical stays on screen until dismissed"}},
        ["title"]),
    "desktop_open": _schema(
        "desktop_open",
        "Open a URL in the default browser, a file/folder in its default application, or launch an installed desktop application by name (e.g. 'firefox', 'dolphin', 'spotify'). Never pass shell commands here; use the terminal tool for those.",
        {"target": {"type": "string", "description": "URL, absolute/~ file path, or application name"},
         "kind": {"type": "string", "enum": ["auto", "url", "file", "app"], "description": "Force how target is interpreted (default auto)"}},
        ["target"]),
    "clipboard": _schema(
        "clipboard",
        "Read or replace the desktop clipboard text.",
        {"action": {"type": "string", "enum": ["get", "set"]},
         "text": {"type": "string", "description": "Text to place on the clipboard (set only)"}},
        ["action"]),
    "notes_manage": _schema(
        "notes_manage",
        "Durable notes and to-dos kept as markdown files in the user's home (survive across sessions, unlike todo_list). add new items, list open/done ones, search, mark done, update or delete. Use for anything the user wants remembered as a task or note.",
        {"action": {"type": "string", "enum": ["add", "list", "search", "get", "done", "undone", "update", "delete"]},
         "id": {"type": "string", "description": "Note id (or unique prefix) for get/done/undone/update/delete"},
         "title": {"type": "string"},
         "body": {"type": "string", "description": "Markdown body"},
         "append": {"type": "string", "description": "Text to append to the body (update)"},
         "tags": {"type": "array", "items": {"type": "string"}},
         "due": {"type": "string", "description": "Optional due date, YYYY-MM-DD"},
         "status": {"type": "string", "enum": ["open", "done", "all"], "description": "list filter (default open)"},
         "tag": {"type": "string", "description": "list filter by tag"},
         "query": {"type": "string", "description": "search terms"},
         "limit": {"type": "integer"}},
        ["action"]),
    "set_reminder": _schema(
        "set_reminder",
        "Schedule a desktop reminder for the user. THE tool for 'remind me ...', timers and recurring nudges. "
        "Fires as a desktop notification at the given time with no further action from you. "
        "Never emulate reminders with the terminal (sleep) — use this.",
        {"action": {"type": "string", "enum": ["create", "list", "cancel"], "description": "default create"},
         "when": {"type": "string", "description": "REQUIRED for create. e.g. 'in 20 minutes', 'in 2 hours', 'at 15:30', 'tomorrow at 9am', 'friday at 10', 'every weekday at 9am', 'every 30 minutes'"},
         "message": {"type": "string", "description": "REQUIRED for create. What to show the user, in their words (e.g. 'Stretch', 'Call the dentist')"},
         "id": {"type": "string", "description": "reminder id for cancel (from list)"}},
        []),
    "lan_devices": _schema(
        "lan_devices",
        "List devices on the local network (IP, MAC, name when known). Use when the user asks what is on the "
        "network, whether a device is connected, or wants an IP for a device name. scan=true pings the whole "
        "subnet first for a fresh list (a few seconds).",
        {"scan": {"type": "boolean", "description": "ping-sweep the local subnet first (default false)"}}, []),
    "host_check": _schema(
        "host_check",
        "Is a host up? Ping plus optional TCP port checks (router, NAS, printer, server, any IP or name).",
        {"host": {"type": "string", "description": "hostname or IP"},
         "ports": {"type": "array", "items": {"type": "integer"}, "description": "TCP ports to test, e.g. [22, 80, 443]"}},
        ["host"]),
    "wake_on_lan": _schema(
        "wake_on_lan",
        "Wake a sleeping machine on the LAN by sending a Wake-on-LAN magic packet to its MAC address. "
        "If you do not know the MAC, look in the user's notes or ask; lan_devices shows MACs of devices that are awake.",
        {"mac": {"type": "string", "description": "MAC address like aa:bb:cc:dd:ee:ff"},
         "broadcast": {"type": "string", "description": "broadcast address (default 255.255.255.255)"}},
        ["mac"]),
    "http_request": _schema(
        "http_request",
        "Call any HTTP/JSON API the user names (REST endpoints, self-hosted services, public APIs). "
        "Authenticate with auth_env=NAME, the name of a key the user stored in the profile .env (e.g. GITHUB_TOKEN); "
        "the value is injected as a header and never shown to you. GET/HEAD run directly; other methods need "
        "confirm=true after the user agreed. Prefer this over the browser for anything that returns JSON.",
        {"method": {"type": "string", "enum": ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]},
         "url": {"type": "string"},
         "headers": {"type": "object", "description": "extra request headers"},
         "json": {"description": "JSON body (object or array)"},
         "body": {"type": "string", "description": "raw body when not JSON"},
         "auth_env": {"type": "string", "description": "name of the .env variable holding the token/key"},
         "auth_header": {"type": "string", "description": "header to put it in (default Authorization)"},
         "auth_scheme": {"type": "string", "description": "prefix before the value (default Bearer; use '' for none, 'token' for GitHub classic)"},
         "confirm": {"type": "boolean", "description": "required true for POST/PUT/PATCH/DELETE after the user confirmed"},
         "timeout": {"type": "number"}, "char_limit": {"type": "integer", "description": "max response chars (default 20000)"}},
        ["url"]),
    "docs_search": _schema(
        "docs_search",
        "Full-text search over the user's own document folders (Documents by default; markdown, text, code, PDF, Word, spreadsheets). Returns file paths with snippets. Always follow up with read_file on the best hit before answering.",
        {"query": {"type": "string", "description": "2-5 distinctive keywords"},
         "limit": {"type": "integer", "description": "max results (default 8)"},
         "reindex": {"type": "boolean", "description": "refresh the index first (slow on large folders)"}},
        ["query"]),
}

HANDLERS = {"desktop_notify": desktop_notify, "desktop_open": desktop_open, "clipboard": clipboard,
            "notes_manage": notes_manage, "docs_search": docs_search, "set_reminder": reminders.set_reminder,
            "lan_devices": network.lan_devices, "host_check": network.host_check, "wake_on_lan": network.wake_on_lan,
            "http_request": network.http_request}
EMOJI = {"desktop_notify": "🔔", "desktop_open": "🚀", "clipboard": "📋", "notes_manage": "📝", "docs_search": "📚",
         "set_reminder": "⏰", "lan_devices": "🖧", "host_check": "📡", "wake_on_lan": "⚡", "http_request": "🌐"}


# ----------------------------------------------------------------------------- slash command + hook
def _slash_notes(raw: str) -> str:
    argv = (raw or "").strip().split(maxsplit=1)
    sub = argv[0] if argv else "list"
    rest = argv[1] if len(argv) > 1 else ""
    if sub == "add" and rest:
        n = notes.add(rest)
        return f"added {n.id}: {n.title}"
    if sub == "done" and rest:
        n = notes.get(rest)
        if not n:
            return "no such note"
        n.done = True; notes.save(n)
        return f"done: {n.title}"
    if sub == "search" and rest:
        hits = notes.search(rest)
        return "\n".join(f"{n.id}  {n.title}" for n, _ in hits) or "no matches"
    items = notes.list_notes("all" if sub == "all" else "open")
    if not items:
        return "no open notes"
    return "\n".join(f"[{'x' if n.done else ' '}] {n.id}  {n.title}" + (f"  (due {n.due})" if n.due else "") for n in items[:30])


_last_refresh = 0.0
_refresh_lock = threading.Lock()


def _on_session_end(**_: Any) -> None:
    """Refresh the docs index in the background at most every 10 minutes."""
    global _last_refresh
    with _refresh_lock:
        if time.time() - _last_refresh < docs_index.STALE_SECONDS:
            return
        _last_refresh = time.time()
    threading.Thread(target=lambda: docs_index.reindex(), name="mikronous-docs-reindex", daemon=True).start()


# ----------------------------------------------------------------------------- registration
def _already_registered(ctx) -> set[str]:
    """Tool names Hermes already holds for this plugin (pre-registered via provides_tools)."""
    try:
        return set(getattr(ctx._manager, "_plugin_tool_names", ()))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return set()


def _json_result(handler):
    """Hermes accepts only str (or a multimodal envelope) from a tool handler; our handlers return dicts."""
    def wrapped(args: dict, **kw: Any):
        result = handler(args, **kw)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    wrapped.__name__ = getattr(handler, "__name__", "tool")
    wrapped.__doc__ = handler.__doc__
    return wrapped


def register_tools(ctx) -> None:
    done = _already_registered(ctx)
    if done >= set(HANDLERS):
        return  # Hermes imported tools.py itself (deferred path); register() must not do it twice
    for name, handler in HANDLERS.items():
        if name in done:
            continue
        ctx.register_tool(name=name, toolset=TOOLSET, schema=SCHEMAS[name], handler=_json_result(handler),
                          description=SCHEMAS[name]["description"], emoji=EMOJI[name])
    # Shipped skills are NOT registered here: plugin skills stay out of the system prompt's
    # <available_skills>. install.sh symlinks <repo>/skills into the profile's skills/mikronous/
    # category instead, so the model sees them like any installed skill.
    if hasattr(ctx, "register_command"):
        try:
            ctx.register_command("notes", _slash_notes, description="Mikronous notes: list | all | add <text> | done <id> | search <q>",
                                 args_hint="[list|all|add <text>|done <id>|search <q>]")
        except Exception as exc:  # noqa: BLE001
            logger.debug("mikronous: /notes not registered: %s", exc)
    if hasattr(ctx, "register_hook"):
        for hook_name, cb in (("on_session_end", _on_session_end), ("pre_tool_call", reminders.guard_terminal_reminders)):
            try:
                ctx.register_hook(hook_name, cb)
            except Exception as exc:  # noqa: BLE001
                logger.debug("mikronous: %s hook not registered: %s", hook_name, exc)
