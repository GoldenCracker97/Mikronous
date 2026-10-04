"""Client for the Hermes API server's runs API (POST /v1/runs + SSE events + approvals).

Pure Python (httpx, no Qt) so it can be tested without a display. ``RunStream`` parses the
``GET /v1/runs/{id}/events`` Server-Sent Events stream into ``RunEvent`` objects; the Qt layer
(``chat_window.ChatWorker``) runs it in a thread and turns events into signals.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import httpx

from mikronous_cli.paths import PROFILE, gateway

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 120.0        # SSE keepalives arrive well inside this
CONTROL_TIMEOUT = 4.0       # stop / approve / health: never let a wedged gateway hold the window
RUN_TIMEOUT = 1800.0        # hard cap on one turn


class GatewayError(RuntimeError):
    pass


@dataclass
class RunEvent:
    name: str                      # message.delta, tool.started, approval.request, run.completed, ...
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.name in ("run.completed", "run.failed", "run.cancelled", "run.interrupted")


def new_session_id() -> str:
    return f"tray-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"


class HermesClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        if base_url is None or api_key is None:
            gw = gateway()
            base_url, api_key = base_url or gw.v1, api_key or gw.api_key
        self.v1 = base_url.rstrip("/")
        self.api_root = self.v1[:-3] if self.v1.endswith("/v1") else self.v1
        self.api_key = api_key
        self._client = httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
                                    headers={"Authorization": f"Bearer {api_key}"})
        # Control calls (stop, approve, health) must never hang the window behind a wedged turn.
        self._control = httpx.Client(timeout=httpx.Timeout(CONTROL_TIMEOUT, connect=CONNECT_TIMEOUT),
                                     headers={"Authorization": f"Bearer {api_key}"})

    # ----------------------------------------------------------------- helpers
    def _json(self, method: str, url: str, *, control: bool = False, **kw) -> dict:
        try:
            r = (self._control if control else self._client).request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise GatewayError(f"gateway unreachable ({exc.__class__.__name__}); run `mik doctor`") from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", {}).get("message") or r.text[:200]
            except Exception:  # noqa: BLE001
                detail = r.text[:200]
            raise GatewayError(f"HTTP {r.status_code}: {detail}")
        return r.json() if r.content else {}

    # ----------------------------------------------------------------- runs
    def start_run(self, text: str, session_id: str) -> str:
        body = {"model": PROFILE, "input": text, "session_id": session_id}
        data = self._json("POST", f"{self.v1}/runs", json=body,
                          headers={"Idempotency-Key": uuid.uuid4().hex})
        run_id = data.get("run_id")
        if not run_id:
            raise GatewayError(f"no run_id in response: {data}")
        return run_id

    def run_status(self, run_id: str) -> dict:
        return self._json("GET", f"{self.v1}/runs/{run_id}")

    def stop(self, run_id: str) -> None:
        self._json("POST", f"{self.v1}/runs/{run_id}/stop", control=True)

    def approve(self, run_id: str, choice: str, request_id: str | None = None) -> None:
        body: dict[str, Any] = {"choice": choice}
        if request_id:
            body["request_id"] = request_id
        self._json("POST", f"{self.v1}/runs/{run_id}/approval", json=body, control=True)

    def events(self, run_id: str, *, should_stop: Callable[[], bool] | None = None) -> Iterator[RunEvent]:
        """Yield events until a terminal one (or the stream closes). Reconnects with Last-Event-ID."""
        last_seq = -1
        deadline = time.monotonic() + RUN_TIMEOUT
        while time.monotonic() < deadline:
            headers = {"Accept": "text/event-stream"}
            if last_seq >= 0:
                headers["Last-Event-ID"] = str(last_seq)
            try:
                with self._client.stream("GET", f"{self.v1}/runs/{run_id}/events", headers=headers) as r:
                    if r.status_code >= 400:
                        raise GatewayError(f"HTTP {r.status_code} on event stream")
                    closed = False
                    for ev in _parse_sse(r.iter_lines(), should_stop):
                        if ev is None:        # ": stream closed" comment
                            closed = True
                            break
                        if "seq" in ev.data:
                            last_seq = int(ev.data["seq"])
                        yield ev
                        if ev.terminal:
                            return
                    if should_stop and should_stop():
                        yield RunEvent("run.cancelled", {"output": ""})     # the user pressed CEASE; no poll needed
                        return
                    if closed:
                        break
            except httpx.ReadTimeout:
                continue                       # keepalive gap; reconnect from last_seq
            except httpx.HTTPError as exc:
                raise GatewayError(f"event stream lost: {exc.__class__.__name__}") from exc
        # Stream closed without a terminal event (e.g. buffer expired): fall back to polling.
        status = self.run_status(run_id)
        st = status.get("status", "failed")
        name = {"completed": "run.completed", "cancelled": "run.cancelled", "stopping": "run.cancelled",
                "interrupted": "run.interrupted"}.get(st, "run.failed")
        yield RunEvent(name, status)

    # ----------------------------------------------------------------- session chat with images
    def session_chat_events(self, session_id: str, text: str, image_paths: list[str], *,
                            should_stop: Callable[[], bool] | None = None) -> Iterator[RunEvent]:
        """One turn through ``POST /api/sessions/{id}/chat/stream`` with inline images (data URLs), yielding
        the same event names as :meth:`events` so the window code does not care which path ran."""
        parts: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        for p in image_paths:
            parts.append({"type": "input_image", "image_url": image_data_url(p)})
        body = {"message": parts}
        created = False
        try:
            while True:
                with self._client.stream("POST", f"{self.api_root}/api/sessions/{session_id}/chat/stream", json=body,
                                         headers={"Accept": "text/event-stream"}, timeout=httpx.Timeout(RUN_TIMEOUT, connect=CONNECT_TIMEOUT)) as r:
                    if r.status_code >= 400:
                        detail = ""
                        try:
                            detail = (r.read().decode("utf-8", "replace"))[:300]
                        except Exception:  # noqa: BLE001
                            pass
                        if r.status_code == 404 and "session_not_found" in detail and not created:
                            created = True                 # a fresh tray chat has no row yet; /v1/runs makes one lazily, this path does not
                            self.ensure_session(session_id)
                            continue
                        raise GatewayError(f"HTTP {r.status_code} on session chat stream: {detail}")
                    final_text = ""
                    for ev in _parse_sse(r.iter_lines(), should_stop):
                        if ev is None:
                            break
                        if ev.name == "assistant.completed":          # the only place the final text travels
                            final_text = str(ev.data.get("content") or ev.data.get("text") or "")
                            continue
                        norm = normalize_session_event(ev)
                        if norm is None:
                            continue
                        if norm.terminal and not norm.data.get("output"):
                            norm.data["output"] = final_text
                        yield norm
                        if norm.terminal:
                            return
                    if should_stop and should_stop():
                        yield RunEvent("run.cancelled", {"output": final_text})
                        return
                break
        except httpx.HTTPError as exc:
            raise GatewayError(f"session chat stream lost: {exc.__class__.__name__}") from exc
        yield RunEvent("run.failed", {"error": "the stream ended without a result"})

    def ensure_session(self, session_id: str) -> None:
        """Create the session row for an id the tray minted (POST /api/sessions); an existing row is fine."""
        try:
            self._json("POST", f"{self.api_root}/api/sessions", json={"id": session_id, "source": "api_server"})
        except GatewayError as exc:
            if "409" not in str(exc) and "exists" not in str(exc).lower():
                raise

    # ----------------------------------------------------------------- sessions
    def session_messages(self, session_id: str, limit: int = 200) -> list[dict]:
        """Chat history (user/assistant text only) for repopulating a window after a restart."""
        try:
            data = self._json("GET", f"{self.api_root}/api/sessions/{session_id}/messages",
                              params={"limit": limit, "order": "oldest"})
        except GatewayError:
            return []
        out = []
        for m in data.get("data") or []:
            role, content = m.get("role"), m.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                out.append({"role": role, "content": content})
        return out

    def list_sessions(self, limit: int = 100) -> list[dict]:
        """Persisted sessions, most recent first (``/api/sessions``). [] when the gateway is down."""
        try:
            data = self._json("GET", f"{self.api_root}/api/sessions", params={"limit": limit})
        except GatewayError:
            return []
        return [s for s in (data.get("data") or []) if isinstance(s, dict) and s.get("id")]

    def rename_session(self, session_id: str, title: str) -> None:
        self._json("PATCH", f"{self.api_root}/api/sessions/{session_id}", json={"title": title})

    def delete_session(self, session_id: str) -> None:
        self._json("DELETE", f"{self.api_root}/api/sessions/{session_id}")

    # ----------------------------------------------------------------- cron jobs (routines)
    def list_jobs(self, include_disabled: bool = True) -> list[dict]:
        """Every cron job the gateway knows (``/api/jobs``). [] when the gateway is down."""
        try:
            data = self._json("GET", f"{self.api_root}/api/jobs", params={"include_disabled": str(include_disabled).lower()})
        except GatewayError:
            return []
        return [j for j in (data.get("jobs") or []) if isinstance(j, dict) and j.get("id")]

    def create_job(self, name: str, schedule: str, prompt: str, *, deliver: str = "mikronous",
                   skills: list[str] | None = None) -> dict:
        body: dict[str, Any] = {"name": name, "schedule": schedule, "prompt": prompt, "deliver": deliver}
        if skills:
            body["skills"] = skills
        return self._json("POST", f"{self.api_root}/api/jobs", json=body).get("job") or {}

    def update_job(self, job_id: str, **fields: Any) -> dict:
        return self._json("PATCH", f"{self.api_root}/api/jobs/{job_id}", json=fields).get("job") or {}

    def delete_job(self, job_id: str) -> None:
        self._json("DELETE", f"{self.api_root}/api/jobs/{job_id}")

    def job_action(self, job_id: str, action: str) -> dict:
        """action: pause | resume | run"""
        return self._json("POST", f"{self.api_root}/api/jobs/{job_id}/{action}")

    def health(self) -> dict:
        try:
            return self._json("GET", f"{self.v1}/models", control=True)
        except GatewayError as exc:
            return {"error": str(exc)}

    def close(self) -> None:
        self._client.close()
        self._control.close()


MAX_IMAGE_BYTES = 6 * 1024 * 1024


def image_data_url(path: str) -> str:
    import base64
    import mimetypes
    import os
    size = os.path.getsize(path)
    if size > MAX_IMAGE_BYTES:
        raise GatewayError(f"image is {size // (1024 * 1024)} MB; captures are downscaled to stay under {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode('ascii')}"


def normalize_session_event(ev: RunEvent) -> RunEvent | None:
    """Session-stream event names → the /v1/runs vocabulary the window understands. None = ignore."""
    d, name = ev.data, ev.name
    if name == "assistant.delta":
        return RunEvent("message.delta", {"delta": d.get("delta", "")})
    if name == "assistant.commentary":
        return RunEvent("message.interim", {"text": d.get("text", ""), "already_streamed": d.get("already_streamed")})
    if name in ("tool.started", "tool.completed", "tool.failed"):
        return RunEvent("tool.started" if name == "tool.started" else "tool.completed",
                        {"tool": d.get("tool_name") or d.get("tool") or "", "preview": d.get("preview") or "",
                         "error": name == "tool.failed" or bool(d.get("error"))})
    if name in ("approval.request", "run.started"):
        return RunEvent(name, d)
    if name in ("run.completed", "run.failed", "run.cancelled", "run.interrupted"):
        out = d.get("final_response") or d.get("output") or ""
        err = d.get("error") or d.get("turn_exit_reason") or ""
        return RunEvent(name, {**d, "output": out, **({"error": err} if err and name != "run.completed" else {})})
    if name == "error":
        return RunEvent("run.failed", {"error": d.get("message") or "the run failed"})
    return None            # message.started, tool.progress, assistant.completed, done


def _parse_sse(lines: Iterator[str], should_stop: Callable[[], bool] | None) -> Iterator[RunEvent | None]:
    """Minimal SSE parser: ``data:`` lines (joined per blank-line frame) become RunEvent;
    the ``: stream closed`` comment yields None; other comments are ignored."""
    data_lines: list[str] = []
    event_name = ""
    for raw in lines:
        if should_stop and should_stop():
            return
        line = raw.rstrip("\r")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines, name, event_name = [], event_name, ""
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield RunEvent(str(obj.get("event") or name or ""), obj)
            continue
        if line.startswith(":"):
            if "stream closed" in line:
                yield None
                return
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.startswith("event:"):
            event_name = line[6:].strip()         # the session stream names events here; /v1/runs puts it in the payload
