from mikronous_tray.hermes_client import _parse_sse


def test_parse_sse_frames_and_close():
    frames = [": open", "", "id: 0", 'data: {"event": "message.delta", "run_id": "r", "delta": "Hel", "seq": 0}', "",
              ": keepalive", "", 'data: {"event": "tool.started", "tool": "terminal", "preview": "ls", "seq": 1}', "",
              'data: {"event": "run.completed", "output": "Hello", "seq": 2}', "", ": stream closed", ""]
    evs = list(_parse_sse(iter(frames), None))
    assert [e.name if e else None for e in evs] == ["message.delta", "tool.started", "run.completed", None]
    assert evs[0].data["delta"] == "Hel"
    assert evs[2].terminal


def test_parse_sse_multiline_data_and_bad_json():
    frames = ["data: {\"event\":", 'data: "x"}', "", "data: not json", "", 'data: {"event": "run.failed", "error": "boom"}', ""]
    evs = [e for e in _parse_sse(iter(frames), None) if e]
    assert [e.name for e in evs] == ["x", "run.failed"]


def test_event_lines_and_session_normalisation():
    from mikronous_tray.hermes_client import RunEvent, _parse_sse, normalize_session_event
    lines = ["event: run.started", 'data: {"run_id": "run_9", "seq": 1}', "",
             "event: assistant.delta", 'data: {"delta": "Hi", "seq": 2}', "",
             'data: {"event": "message.delta", "delta": "!"}', "",
             "event: tool.failed", 'data: {"tool_name": "terminal", "preview": "boom"}', "",
             "event: done", "data: {}", ""]
    evs = [e for e in _parse_sse(iter(lines), None) if e is not None]
    assert [e.name for e in evs] == ["run.started", "assistant.delta", "message.delta", "tool.failed", "done"]
    assert evs[0].data["run_id"] == "run_9"
    norm = [normalize_session_event(e) for e in evs]
    assert norm[1].name == "message.delta" and norm[1].data["delta"] == "Hi"
    assert norm[3].name == "tool.completed" and norm[3].data == {"tool": "terminal", "preview": "boom", "error": True}
    assert norm[4] is None
    assert normalize_session_event(RunEvent("error", {"message": "nope"})).name == "run.failed"
    done = normalize_session_event(RunEvent("run.completed", {"final_response": "ok"}))
    assert done.terminal and done.data["output"] == "ok"


def test_session_chat_creates_missing_session_and_retries():
    import json
    import httpx
    from mikronous_tray.hermes_client import HermesClient
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, json.loads(req.content or b"{}")))
        if req.url.path.endswith("/chat/stream"):
            if not any(m == "POST" and p == "/api/sessions" for m, p, _ in seen):
                return httpx.Response(404, json={"error": {"message": "Session not found: tray-x", "code": "session_not_found"}})
            body = ('event: run.started\ndata: {"run_id": "run_1"}\n\n'
                    'event: assistant.delta\ndata: {"delta": "A window."}\n\n'
                    'event: run.completed\ndata: {"final_response": "A window."}\n\n')
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())
        if req.url.path == "/api/sessions":
            return httpx.Response(200, json={"id": "tray-x"})
        return httpx.Response(500)

    c = HermesClient("http://gw/v1", "k")
    c._client = httpx.Client(transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer k"})
    evs = list(c.session_chat_events("tray-x", "What is this?", []))
    assert [e.name for e in evs] == ["run.started", "message.delta", "run.completed"]
    posts = [(m, p, b) for m, p, b in seen if p == "/api/sessions"]
    assert posts == [("POST", "/api/sessions", {"id": "tray-x", "source": "api_server"})]
    assert sum(1 for _m, p, _b in seen if p.endswith("/chat/stream")) == 2
    first_body = next(b for m, p, b in seen if p.endswith("/chat/stream"))
    assert first_body["message"][0] == {"type": "input_text", "text": "What is this?"}
