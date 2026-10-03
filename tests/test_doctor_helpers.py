import sys

import pytest

from mikronous_cli import doctor


@pytest.mark.skipif(sys.platform == "win32", reason="KDE shortcut file is Linux-only")
def test_shortcut_key_kf5_kf6_and_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config").mkdir()
    cases = [
        ("[kwin]\nfoo=bar\n\n[mikronous.desktop]\n_k_friendly_name=Mikronous\n_launch=Meta+Space,none,Mikronous\n", "Meta+Space"),
        ("[services][mikronous.desktop]\n_launch=Meta+Space\nselection=Meta+Shift+Space\n", "Meta+Space"),
        ("[kwin]\nx=y\n", ""),
    ]
    for text, expected in cases:
        (tmp_path / ".config" / "kglobalshortcutsrc").write_text(text)
        assert doctor._shortcut_key() == expected
    assert doctor._shortcut_key("selection") == ""
    (tmp_path / ".config" / "kglobalshortcutsrc").write_text(
        "[mikronous.desktop]\n_k_friendly_name=Mikronous\n_launch=Meta+Space,none,Mikronous\nselection=Meta+Shift+Space,none,Act\n")
    assert doctor._shortcut_key("selection") == "Meta+Shift+Space" and doctor._shortcut_key() == "Meta+Space"


def test_tray_running_false_without_socket(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    if sys.platform != "win32":
        assert doctor._tray_running() is False
