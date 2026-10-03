"""`mik ask "<prompt>"` — one question through the real gateway (full toolset, same path as the tray).

Non-streaming POST to the profile's OpenAI-compatible endpoint; prints the final answer.
`--session <id>` keeps server-side history across calls (X-Hermes-Session-Id).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from .paths import PROFILE, gateway


def ask(prompt: str, session: str | None = None, timeout: float = 600.0) -> str:
    gw = gateway()
    if not gw.api_key:
        raise RuntimeError("no API_SERVER_KEY for the mikronous profile; run scripts/install.sh")
    body = json.dumps({"model": PROFILE, "messages": [{"role": "user", "content": prompt}], "stream": False}).encode()
    headers = {"Authorization": f"Bearer {gw.api_key}", "Content-Type": "application/json"}
    if session:
        headers["X-Hermes-Session-Id"] = session
    req = urllib.request.Request(f"{gw.v1}/chat/completions", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"gateway returned HTTP {exc.code} for {gw.v1}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"gateway unreachable at {gw.v1} ({exc.reason}); run `mik doctor`") from exc
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return json.dumps(data, indent=2)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="mik ask", description="Ask the assistant one question through the gateway")
    p.add_argument("prompt", nargs="+")
    p.add_argument("--session", help="session id to continue (any string you choose)")
    p.add_argument("--timeout", type=float, default=600.0)
    a = p.parse_args(argv)
    try:
        print(ask(" ".join(a.prompt), a.session, a.timeout))
        return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
