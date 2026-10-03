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
