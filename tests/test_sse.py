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
