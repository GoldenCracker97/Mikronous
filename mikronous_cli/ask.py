"""`mik ask "<prompt>"` — one question through the real gateway (full toolset, same path as the tray).

Uses the OpenAI Responses endpoint so the tool calls the agent made are visible with `-v`.
`--session <id>` keeps server-side history across calls (X-Hermes-Session-Id).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from .paths import PROFILE, gateway


def _post(url: str, body: dict, key: str, session: str | None, timeout: float) -> dict:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if session:
        headers["X-Hermes-Session-Id"] = session
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"gateway returned HTTP {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"gateway unreachable at {url} ({exc.reason}); run `mik doctor`") from exc


def ask(prompt: str, session: str | None = None, timeout: float = 600.0) -> tuple[str, list[dict]]:
    """Returns (final_text, tool_calls) where tool_calls = [{name, arguments, output}]."""
    gw = gateway()
    if not gw.api_key:
        raise RuntimeError("no API_SERVER_KEY for the mikronous profile; run scripts/install.sh")
    data = _post(f"{gw.v1}/responses", {"model": PROFILE, "input": prompt, "store": False}, gw.api_key, session, timeout)
    text_parts: list[str] = []
    calls: list[dict] = []
    by_call: dict[str, dict] = {}
    for item in data.get("output") or []:
        t = item.get("type")
        if t == "function_call":
            entry = {"name": item.get("name"), "arguments": item.get("arguments"), "output": None}
            calls.append(entry)
            if item.get("call_id"):
                by_call[item["call_id"]] = entry
        elif t == "function_call_output":
            out = item.get("output")
            if isinstance(out, list):
                out = " ".join(str(o.get("text", o)) if isinstance(o, dict) else str(o) for o in out)
            entry = by_call.get(item.get("call_id"))
            if entry is not None:
                entry["output"] = out
        elif t == "message" and item.get("phase") != "commentary":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                    text_parts.append(part.get("text", ""))
    if not text_parts and data.get("output_text"):
        text_parts.append(str(data["output_text"]))
    if not text_parts and not calls:
        text_parts.append(json.dumps(data, indent=2))
    return "\n".join(p for p in text_parts if p).strip(), calls


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="mik ask", description="Ask the assistant one question through the gateway")
    p.add_argument("prompt", nargs="+")
    p.add_argument("--session", help="session id to continue (any string you choose)")
    p.add_argument("-v", "--verbose", action="store_true", help="show the tool calls the agent made")
    p.add_argument("--timeout", type=float, default=600.0)
    a = p.parse_args(argv)
    try:
        text, calls = ask(" ".join(a.prompt), a.session, a.timeout)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if a.verbose:
        for c in calls:
            args = c["arguments"] if isinstance(c["arguments"], str) else json.dumps(c["arguments"])
            print(f"[tool] {c['name']}({(args or '')[:300]})", file=sys.stderr)
            if c["output"]:
                print(f"       -> {str(c['output'])[:300]}", file=sys.stderr)
        if not calls:
            print("[tool] (no tool calls)", file=sys.stderr)
    print(text)
    return 0
