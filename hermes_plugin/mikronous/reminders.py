"""set_reminder: a purpose-built reminder tool for small local models.

Creates a Hermes cron job in *no-agent* mode (a tiny script whose stdout is the reminder text,
delivered verbatim to the `mikronous` desktop platform). No LLM call at fire time, no
embellishment, and the model never has to learn cronjob_manage's full surface.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PLATFORM = "mikronous"
SCRIPT_PREFIX = "mik-reminder-"
_UNITS = {"s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
          "m": "m", "min": "m", "mins": "m", "minute": "m", "minutes": "m",
          "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
          "d": "d", "day": "d", "days": "d", "w": "w", "week": "w", "weeks": "w"}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 - outside Hermes
        return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def _parse_clock(text: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text.strip().lower())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        return None
    return h, mi


_WHEN_ALIASES = ("when", "time", "at", "in", "schedule", "delay", "after", "datetime", "due")
_MESSAGE_ALIASES = ("message", "text", "reminder", "title", "body", "task", "note", "content")
_EMBEDDED_WHEN = re.compile(
    r"\b(in\s+(?:\d+(?:\.\d+)?|an?|one)\s*(?:" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")"
    r"|(?:every\s+(?:\d+(?:\.\d+)?\s*(?:" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")|[a-z]+)"
    r"(?:\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)"
    r"|(?:(?:today|tomorrow|tonight|" + "|".join(_WEEKDAYS) + r")\s+)?at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
    re.IGNORECASE)
_REMIND_PREFIX = re.compile(r"^(?:please\s+)?(?:remind\s+me\s+)?(?:to\s+)?", re.IGNORECASE)


def extract_when_and_message(args: dict) -> tuple[str, str]:
    """Tolerate small models: accept alias field names and, when ``when`` is missing, pull the time
    phrase out of the message ("Remind me in 1 minute to blink" -> when="in 1 minute", message="blink")."""
    def first(keys):
        for k in keys:
            v = args.get(k)
            if isinstance(v, (int, float)) and k in ("in", "delay", "after"):
                return f"in {v} minutes"
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""
    message = " ".join(first(_MESSAGE_ALIASES).split())
    when = first(_WHEN_ALIASES)
    if not when and message:
        m = _EMBEDDED_WHEN.search(message)
        if m:
            when = m.group(0)
            message = " ".join((message[:m.start()] + " " + message[m.end():]).split())
    if message:
        message = _REMIND_PREFIX.sub("", message, count=1).strip(" ,.-") or message
        message = re.sub(r"^(?:to\s+)", "", message).strip() or message
    return message, when


def normalize_when(text: str, now: datetime | None = None) -> str:
    """Turn everyday phrasing into Hermes's schedule grammar.

    in 2 minutes / in 1.5 hours  -> in 2m / in 90m          (one-shot by duration)
    at 15:30 / at 3pm / tomorrow at 9 -> ISO timestamp     (one-shot)
    every day at 9am / every weekday at 9 / every monday 8:30 -> Hermes natural cron (pass-through)
    every 30 minutes -> every 30m                           (recurring interval)
    anything else is passed through unchanged (cron expressions, ISO timestamps).
    """
    now = now or datetime.now()
    s = " ".join((text or "").strip().lower().replace(",", " ").split())
    if not s:
        raise ValueError("when is empty")

    m = re.fullmatch(r"in\s+(\d+(?:\.\d+)?)\s*([a-z]+)", s)
    if m and m.group(2) in _UNITS:
        n, unit = float(m.group(1)), _UNITS[m.group(2)]
        minutes = {"s": n / 60, "m": n, "h": n * 60, "d": n * 1440, "w": n * 10080}[unit]
        return f"in {max(1, int(round(minutes)))}m"
    m = re.fullmatch(r"in\s+(?:an?|one)\s+([a-z]+)", s)
    if m and m.group(1) in _UNITS:
        return normalize_when(f"in 1 {m.group(1)}", now)

    m = re.fullmatch(r"every\s+(\d+(?:\.\d+)?)\s*([a-z]+)", s)
    if m and m.group(2) in _UNITS:
        return normalize_when(f"in {m.group(1)} {m.group(2)}", now).replace("in ", "every ", 1)
    if s.startswith("every ") or re.fullmatch(r"(weekdays?|weekends?|daily|hourly)(\s+at\s+.+)?", s):
        return s  # Hermes parses "every monday 9am", "weekdays at 9am", ...

    # one-shot clock times: "at 15:30", "3pm", "tomorrow at 9am", "today 18:00", "friday at 10"
    m = re.fullmatch(r"(?:(today|tomorrow|tonight|" + "|".join(_WEEKDAYS) + r")\s*)?(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", s)
    if m:
        clock = _parse_clock(m.group(2))
        if clock:
            h, mi = clock
            day = m.group(1) or ""
            target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if day == "tomorrow":
                target += timedelta(days=1)
            elif day in _WEEKDAYS:
                delta = (_WEEKDAYS.index(day) - now.weekday()) % 7
                if delta == 0 and target <= now:
                    delta = 7
                target += timedelta(days=delta)
            elif day == "tonight" and h < 12:
                target += timedelta(hours=12)
            if target <= now and day in ("", "today", "tonight"):
                target += timedelta(days=1)
            return target.strftime("%Y-%m-%dT%H:%M:00")
    return text.strip()


def _scripts_dir() -> Path:
    d = _hermes_home() / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_script(message: str) -> str:
    name = f"{SCRIPT_PREFIX}{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}.sh"
    path = _scripts_dir() / name
    path.write_text("#!/bin/sh\n" + f"printf '%s\\n' {shlex.quote(message)}\n", encoding="utf-8")
    path.chmod(0o755)
    return name


def _cronjob(**kwargs) -> dict:
    from tools.cronjob_tools import cronjob  # type: ignore  # Hermes in-process API
    raw = cronjob(**kwargs)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {"raw": raw}
    return raw if isinstance(raw, dict) else {"raw": raw}


def _is_reminder(job: dict) -> bool:
    return str(job.get("name", "")).startswith("Reminder: ")


def _cleanup_scripts(active_jobs: list[dict]) -> None:
    """Delete reminder scripts whose job no longer exists (one-shots are removed after firing)."""
    try:
        from cron.jobs import list_jobs  # type: ignore
        referenced = {str(j.get("script") or "") for j in list_jobs(include_disabled=True)}
    except Exception:  # noqa: BLE001
        return
    for p in _scripts_dir().glob(f"{SCRIPT_PREFIX}*.sh"):
        if p.name not in referenced:
            try:
                p.unlink()
            except OSError:
                pass


def set_reminder(args: dict, **_: Any) -> dict:
    action = str(args.get("action") or "create").lower()
    try:
        if action == "list":
            res = _cronjob(action="list")
            jobs = [j for j in res.get("jobs", []) if _is_reminder(j)]
            _cleanup_scripts(jobs)
            return {"count": len(jobs), "reminders": [
                {"id": j.get("job_id"), "text": str(j.get("name", ""))[len("Reminder: "):], "schedule": j.get("schedule"),
                 "next_run": j.get("next_run") or j.get("next_run_at"), "enabled": j.get("enabled", True)} for j in jobs]}
        if action in ("cancel", "delete", "remove"):
            rid = str(args.get("id") or "").strip()
            if not rid:
                return {"error": "cancel needs the reminder id (use action=list)"}
            res = _cronjob(action="remove", job_id=rid)
            return {"ok": bool(res.get("success")), "message": res.get("message") or res.get("error")}
        if action != "create":
            return {"error": "action must be create, list or cancel"}

        message, when = extract_when_and_message(args)
        if not message and not when:
            return {"error": "message is required (what to remind the user of) and when, e.g. 'in 20 minutes'"}
        if not when:
            return {"error": "when is required, e.g. 'in 20 minutes', 'at 15:30', 'tomorrow at 9am', 'every weekday at 9am'"}
        if not message:
            return {"error": "message is required (what to remind the user of)"}
        schedule = normalize_when(when)
        script = _write_script(message)
        res = _cronjob(action="create", schedule=schedule, name=f"Reminder: {message[:60]}", script=script,
                       no_agent=True, deliver=PLATFORM)
        if not res.get("success", True) or res.get("error"):
            try:
                (_scripts_dir() / script).unlink()
            except OSError:
                pass
            return {"error": res.get("error") or res.get("message") or "cron refused the reminder",
                    "schedule_tried": schedule}
        job = res.get("job") or {}
        return {"ok": True, "id": job.get("job_id") or res.get("job_id"), "text": message, "schedule": schedule,
                "schedule_display": job.get("schedule"), "next_run": job.get("next_run") or job.get("next_run_at"),
                "delivery": "desktop notification (mikronous platform)"}
    except ImportError as exc:
        return {"error": f"reminders need the Hermes cron module (not available here): {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"reminder failed: {exc.__class__.__name__}: {exc}"}


# --------------------------------------------------------------------------- guard rail
_SLEEP_RE = re.compile(r"\bsleep\s+\d", re.IGNORECASE)
_REMIND_WORDS = re.compile(r"remind|notify|notification|alarm|alert|stretch|timer|desktop_notify|cronjob_manage|set_reminder", re.IGNORECASE)


def guard_terminal_reminders(tool_name: str = "", args: dict | None = None, **_: Any):
    """pre_tool_call hook: stop `sleep N && notify...` style reminders in the terminal tool, and
    direct cronjob_manage calls (set_reminder is the one scheduler this profile exposes)."""
    if tool_name == "cronjob_manage":
        return {"action": "block",
                "message": ("Blocked: use the set_reminder tool for reminders and schedules, e.g. "
                            "set_reminder(action='create', when='in 2 minutes', message='Stretch'); "
                            "set_reminder(action='list') / (action='cancel', id=...) manage them.")}
    if tool_name != "terminal" or not isinstance(args, dict):
        return None
    cmd = str(args.get("command") or "")
    if ("cronjob_manage" in cmd or "set_reminder" in cmd or "desktop_notify" in cmd
            or (_SLEEP_RE.search(cmd) and _REMIND_WORDS.search(cmd))):
        return {"action": "block",
                "message": ("Blocked: reminders must not be run as shell sleep commands or by typing tool names into "
                            "the terminal. Call the set_reminder tool instead, e.g. "
                            "set_reminder(action='create', when='in 2 minutes', message='Stretch'). "
                            "For an immediate popup call desktop_notify.")}
    return None
