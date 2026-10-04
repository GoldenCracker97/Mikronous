"""Media/system control: parsers and command selection with a fake shell (no desktop needed)."""
import pytest

from mikronous import desktop_control as dc


def test_parsers():
    names = "(['org.freedesktop.DBus', 'org.mpris.MediaPlayer2.spotify', 'org.mpris.MediaPlayer2.firefox.instance_12', ':1.5'],)"
    assert dc.mpris_players(names) == ["firefox.instance_12", "spotify"]
    meta = "({'xesam:title': <'Blue Train'>, 'xesam:artist': <['John Coltrane']>},) <'Playing'>"
    assert dc.parse_metadata(meta) == {"title": "Blue Train", "artist": "John Coltrane", "status": "playing"}
    assert dc.parse_volume("Volume: 0.45 [MUTED]") == (45, True)
    assert dc.parse_volume("Volume: 1.00") == (100, False)
    assert dc.parse_volume("Volume: front-left: 29491 /  45% / -20.00 dB,   front-right: 29491 /  45%") == (45, False)
    assert dc.parse_volume("garbage") == (None, False)
    assert dc._clamp("130%") == 100 and dc._clamp(-5) == 0 and dc._clamp("x") is None
    assert dc._gd_int("(int32 1234,)") == 1234 and dc._gd_int("(7,)") == 7


@pytest.fixture
def shell(monkeypatch):
    calls = []
    outputs = {}

    def fake_sh(cmd, timeout=8.0, input_text=None):
        calls.append(cmd)
        for key, (rc, out) in outputs.items():
            if key in " ".join(cmd):
                return rc, out
        return 0, ""
    monkeypatch.setattr(dc, "_sh", fake_sh)
    monkeypatch.setattr(dc, "IS_WINDOWS", False)
    return calls, outputs


def test_volume_flow_with_wpctl(shell, monkeypatch):
    calls, outputs = shell
    monkeypatch.setattr(dc, "_has", lambda t: t == "wpctl")
    outputs["get-volume"] = (0, "Volume: 0.40")
    assert dc.system_control({"action": "volume_get"}) == {"volume": 40, "muted": False, "via": "wpctl"}
    assert dc.system_control({"action": "volume_set", "value": "250"}) == {"ok": True, "volume": 100}
    assert calls[-1][-1] == "1.00"
    assert dc.system_control({"action": "volume_up"}) == {"ok": True, "volume": 50}
    assert dc.system_control({"action": "volume_down", "value": 15}) == {"ok": True, "volume": 25}
    assert dc.system_control({"action": "volume_up", "value": "a bit"}) == {"ok": True, "volume": 50}   # junk step -> default 10
    assert dc.system_control({"action": "mute"}) == {"ok": True, "muted": True} and calls[-1][-1] == "1"
    assert "error" in dc.system_control({"action": "volume_set", "value": "loud"})
    assert "error" in dc.system_control({"action": "reboot"})


def test_media_prefers_playing_player(shell, monkeypatch):
    calls, outputs = shell
    monkeypatch.setattr(dc, "_has", lambda t: t == "gdbus")
    outputs["ListNames"] = (0, "(['org.mpris.MediaPlayer2.vlc', 'org.mpris.MediaPlayer2.spotify'],)")
    outputs["spotify --object-path /org/mpris/MediaPlayer2 --method org.freedesktop.DBus.Properties.Get org.mpris.MediaPlayer2.Player PlaybackStatus"] = (0, "(<'Playing'>,)")
    outputs["vlc --object-path /org/mpris/MediaPlayer2 --method org.freedesktop.DBus.Properties.Get org.mpris.MediaPlayer2.Player PlaybackStatus"] = (0, "(<'Paused'>,)")
    res = dc.media_control({"action": "toggle"})
    assert res == {"ok": True, "player": "spotify", "action": "toggle"}
    assert calls[-1][-1] == "org.mpris.MediaPlayer2.Player.PlayPause" and "org.mpris.MediaPlayer2.spotify" in calls[-1]
    assert dc.media_control({"action": "next", "player": "vlc"})["player"] == "vlc"
    assert "error" in dc.media_control({"action": "next", "player": "elisa"})
    outputs["ListNames"] = (0, "([],)")
    assert "error" in dc.media_control({"action": "status"})


def test_dnd_lock_focus(shell, monkeypatch):
    calls, outputs = shell
    monkeypatch.setattr(dc, "_has", lambda t: t in ("kwriteconfig5", "loginctl", "xdotool"))
    res = dc.system_control({"action": "dnd_on", "value": 30})
    assert res["ok"] and "until" in res and calls[-1][:6] == ["kwriteconfig5", "--notify", "--file", "plasmanotifyrc", "--group", "DoNotDisturb"]
    assert calls[-1][-1].count(",") == 5 and not any(part.startswith("0") and len(part) > 1 for part in calls[-1][-1].split(","))
    assert dc.system_control({"action": "dnd_off"})["ok"] and calls[-1][-1] == "--delete"
    assert dc.system_control({"action": "lock"}) == {"ok": True, "via": "loginctl"}
    outputs["search --name Firefox"] = (0, "123\n456\n")
    assert dc.system_control({"action": "focus_window", "window": "Firefox"}) == {"ok": True, "matches": 2, "via": "xdotool"}
    assert "error" in dc.system_control({"action": "focus_window"})
