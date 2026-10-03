"""Routines = Hermes cron jobs that run the agent on a schedule and deliver to the desktop.

Pure helpers (no Qt) shared by the Settings → Routines tab and `mik routine`. A routine is any job
whose ``deliver`` is the mikronous platform; the no-agent reminders `set_reminder` creates share that
delivery and are listed read-only so the user sees everything that will pop up.
"""

from __future__ import annotations

import datetime as _dt
import time
from dataclasses import dataclass

DELIVER = "mikronous"


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    schedule: str
    prompt: str
    skills: tuple[str, ...] = ()
    hint: str = ""


PRESETS: tuple[Preset, ...] = (
    Preset("briefing", "Morning briefing", "every 1d at 08:00",
           "Run the daily-briefing skill: open notes, today's reminders, one suggested task. Keep it under eight lines.",
           ("daily-briefing",), "Open to-dos, today's reminders and a suggestion, every morning."),
    Preset("watch", "Watch a page", "every 6h",
           "Read https://example.com with web_extract. Compare it with what you remember from the last check and "
           "report only if something changed; otherwise answer exactly: no change.",
           (), "Re-reads a page and tells you only when it changed. Edit the URL and the condition."),
    Preset("weekly", "Weekly notes review", "every 7d at 18:00",
           "List the notes added or completed this week, then suggest which open notes to drop, do, or defer. Under ten lines.",
           (), "A Sunday-evening look at the week's notes."),
    Preset("custom", "Custom", "every 1d at 09:00", "", (), "Your own schedule and prompt."),
)


def preset(key: str) -> Preset:
    for p in PRESETS:
        if p.key == key:
            return p
    return PRESETS[-1]


def is_routine(job: dict) -> bool:
    return str(job.get("deliver") or "").split(":", 1)[0] == DELIVER


def is_reminder(job: dict) -> bool:
    """A no-agent job written by `set_reminder` (a script that prints the message)."""
    return bool(job.get("no_agent")) or str(job.get("name") or "").lower().startswith("reminder")


def schedule_text(job: dict) -> str:
    disp = job.get("schedule_display")
    if disp:
        return str(disp)
    sched = job.get("schedule")
    if isinstance(sched, dict):
        for k in ("display", "value", "expr", "run_at"):
            if sched.get(k):
                return str(sched[k])
    return str(sched or "")


def when_text(ts, now: float | None = None) -> str:
    """'in 2h 05m' / 'in 3d' / 'overdue' / '' for a unix timestamp or ISO string."""
    if ts in (None, "", 0):
        return ""
    now = time.time() if now is None else now
    try:
        t = float(ts)
    except (TypeError, ValueError):
        try:
            t = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return str(ts)[:16]
    delta = t - now
    if delta < -60:
        return "overdue"
    if delta < 60:
        return "now"
    mins = int(delta // 60)
    if mins < 60:
        return f"in {mins}m"
    hours, mins = divmod(mins, 60)
    if hours < 48:
        return f"in {hours}h {mins:02d}m"
    return f"in {hours // 24}d"


def summary(job: dict, now: float | None = None) -> tuple[str, str]:
    """(first line, second line) for a list row."""
    name = str(job.get("name") or "(unnamed)")
    state = str(job.get("state") or ("scheduled" if job.get("enabled", True) else "paused"))
    nxt = when_text(job.get("next_run_at"), now) if state == "scheduled" else state.upper()
    last = job.get("last_status")
    tail = f" · last: {last}" if last else ""
    kind = "reminder" if is_reminder(job) else "routine"
    return name, f"{schedule_text(job)} · {nxt}{tail} · {kind}"


def paused(job: dict) -> bool:
    return not job.get("enabled", True) or str(job.get("state") or "") == "paused"
