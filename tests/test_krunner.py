"""KRunner plugin: query parsing, match building, and the D-Bus interface signatures (no bus needed)."""
import pytest

from mikronous_tray import krunner as K


def test_parse_and_matches():
    assert K.parse_query("mik what time is it") == "what time is it"
    assert K.parse_query("Mikronous: hello") == "hello" and K.parse_query("mik") == "" and K.parse_query("mike x") is None
    assert K.build_matches("firefox") == []
    m = K.build_matches("mik what time is it in Tokyo")
    assert m[0][:5] == ("ask:what time is it in Tokyo", "Ask Mikronous: what time is it in Tokyo", "mikronous", 100, 1.0)
    assert all(isinstance(x[5], dict) for x in m) and len(m) == 1
    ids = [x[0] for x in K.build_matches("mik set")]
    assert ids == ["ask:set", "cmd:settings"]
    assert {x[0] for x in K.build_matches("mik ")} == {v[0] for v in K.COMMANDS.values()}


def test_desktop_entry_and_interface(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "DESKTOP_FILE", tmp_path / "plasma-runner-mikronous.desktop")
    p = K.install_desktop_file()
    text = p.read_text()
    assert "X-Plasma-DBusRunner-Service=org.mikronous.Runner" in text and "X-Plasma-DBusRunner-Path=/runner" in text
    assert "X-Plasma-Runner-Match-Regex=^\\s*mik(ronous)?\\b" in text
    pytest.importorskip("dbus_fast")
    from dbus_fast import Variant
    from dbus_fast.service import ServiceInterface, method

    class Runner(ServiceInterface):          # same signatures as the live one; dbus-fast validates them on class creation
        def __init__(self):
            super().__init__("org.kde.krunner1")

        @method()
        def Match(self, query: "s") -> "a(sssida{sv})":
            return [[a, b, c, d, e, {k: Variant("s", v) for k, v in f.items()}] for a, b, c, d, e, f in K.build_matches(query)]

        @method()
        def Run(self, matchId: "s", actionId: "s"):
            pass

    r = Runner()                             # the decorator validated 'a(sssida{sv})' and 's' at class creation
    names = sorted(m.name for m in ServiceInterface._get_methods(r))
    assert names == ["Match", "Run"]
