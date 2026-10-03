"""`mik tools` — which toolsets the gateway actually exposes to the assistant (GET /v1/toolsets)."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from .paths import gateway


def fetch() -> object:
    gw = gateway()
    req = urllib.request.Request(f"{gw.v1}/toolsets", headers={"Authorization": f"Bearer {gw.api_key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - loopback
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str]) -> int:
    try:
        data = fetch()
    except urllib.error.HTTPError as exc:
        print(f"error: HTTP {exc.code} from {gateway().v1}/toolsets: {exc.read().decode('utf-8', 'replace')[:200]}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"error: gateway unreachable ({exc}); run `mik doctor`", file=sys.stderr)
        return 1
    if "--json" in argv:
        print(json.dumps(data, indent=2))
        return 0
    items = data.get("toolsets") or data.get("data") or data if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = [{"name": k, **(v if isinstance(v, dict) else {"value": v})} for k, v in items.items()]
    if not isinstance(items, list):
        print(json.dumps(data, indent=2))
        return 0
    width = max((len(str(i.get("name") or i.get("id") or "")) for i in items if isinstance(i, dict)), default=10)
    for i in items:
        if not isinstance(i, dict):
            print(i); continue
        name = str(i.get("name") or i.get("id") or "")
        flags = []
        for k in ("enabled", "configured", "available", "has_keys", "default"):
            if k in i:
                flags.append(f"{k}={'yes' if i[k] else 'no'}")
        tools = i.get("tools")
        extra = f"  [{', '.join(map(str, tools))[:120]}]" if isinstance(tools, list) and tools else ""
        print(f"{name:<{width}}  {'  '.join(flags)}{extra}")
    want = {"cronjob", "mikronous", "web", "memory", "terminal", "file"}
    names = {str(i.get("name") or i.get("id")) for i in items if isinstance(i, dict)}
    missing = sorted(want - names)
    if missing:
        print(f"\nnot listed at all: {', '.join(missing)}")
    return 0
