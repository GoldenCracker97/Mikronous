"""`mik routine` — scheduled agent tasks that deliver to the desktop (the Routines tab, from a terminal).

    mik routine list
    mik routine add "every 1d at 08:00" "Run the daily-briefing skill" --name "Morning briefing" [--skill daily-briefing]
    mik routine pause|resume|run|remove <id>
    mik routine presets
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from .paths import gateway


def _req(method: str, path: str, body: dict | None = None) -> dict:
    gw = gateway()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{gw.base_url}{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {gw.api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - local gateway
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("error") or exc.reason
        except Exception:  # noqa: BLE001
            detail = exc.reason
        raise SystemExit(f"gateway said {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"gateway unreachable ({exc.reason}); run `mik doctor`") from None


def main(argv: list[str]) -> int:
    from mikronous_tray import routines as R   # pure helpers, no Qt
    ap = argparse.ArgumentParser(prog="mik routine", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    sub.add_parser("presets")
    p_add = sub.add_parser("add")
    p_add.add_argument("schedule")
    p_add.add_argument("prompt")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--skill", action="append", default=[])
    for name in ("pause", "resume", "run", "remove"):
        sub.add_parser(name).add_argument("id")
    a = ap.parse_args(argv or ["list"])
    if a.cmd == "presets":
        for p in R.PRESETS:
            print(f"{p.key:<9} {p.name:<22} {p.schedule:<20} {p.hint}")
        return 0
    if a.cmd == "list":
        jobs = [j for j in _req("GET", "/api/jobs?include_disabled=true").get("jobs") or [] if R.is_routine(j)]
        if not jobs:
            print("no routines; try: mik routine add \"every 1d at 08:00\" \"Run the daily-briefing skill\" --name \"Morning briefing\"")
            return 0
        for j in jobs:
            first, second = R.summary(j)
            print(f"{j.get('id', ''):<14} {first}\n{'':<14} {second}")
        return 0
    if a.cmd == "add":
        job = _req("POST", "/api/jobs", {"name": a.name, "schedule": a.schedule, "prompt": a.prompt,
                                         "deliver": R.DELIVER, **({"skills": a.skill} if a.skill else {})}).get("job") or {}
        print(f"created {job.get('id', '?')}: {job.get('name')} · {R.schedule_text(job)} · next {R.when_text(job.get('next_run_at'))}")
        return 0
    if a.cmd == "remove":
        _req("DELETE", f"/api/jobs/{a.id}")
        print("removed")
        return 0
    _req("POST", f"/api/jobs/{a.id}/{a.cmd}")
    print({"pause": "paused", "resume": "resumed", "run": "dispatched; the answer arrives on the desktop"}[a.cmd])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
