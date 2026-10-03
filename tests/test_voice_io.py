"""Voice I/O helpers that need no microphone, model or speaker."""
import wave

from mikronous_tray import voice_io as V


def test_recorder_and_player_selection():
    assert V.recorder_command("o.wav", which=lambda t: t == "pw-record")[0] == "pw-record"
    assert V.recorder_command("o.wav", which=lambda t: t == "arecord")[:2] == ["arecord", "-q"]
    assert V.recorder_command("o.wav", which=lambda t: False) is None
    assert V.player_command("o.wav", which=lambda t: t == "pw-play") == ["pw-play", "o.wav"]
    assert V.player_command("o.wav", which=lambda t: False) is None


def test_wav_seconds_and_silence_short_circuit(tmp_path):
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 1600)   # 0.1 s
    assert abs(V.wav_seconds(str(p)) - 0.1) < 1e-6
    assert V.transcribe(str(p)) == ""                     # too short: no model is loaded
    assert V.wav_seconds(str(tmp_path / "missing.wav")) == 0.0


def test_availability_messages(monkeypatch):
    monkeypatch.setattr(V, "IS_WINDOWS", False)
    monkeypatch.setattr(V.shutil, "which", lambda t: None)
    ok, msg = V.stt_available()
    assert ok is False and ("recorder" in msg or "faster-whisper" in msg)
    ok, msg = V.tts_available()
    assert ok is False and ("player" in msg or "piper" in msg)
