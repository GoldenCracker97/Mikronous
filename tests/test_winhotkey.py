import pytest

from mikronous_tray import winhotkey


def test_parse_hotkeys():
    mods, vk = winhotkey.parse("Ctrl+Alt+Space")
    assert mods & winhotkey.MOD_CONTROL and mods & winhotkey.MOD_ALT and vk == 0x20
    mods, vk = winhotkey.parse("Win+Shift+M")
    assert mods & winhotkey.MOD_WIN and mods & winhotkey.MOD_SHIFT and vk == ord("M")
    assert winhotkey.parse("F12")[1] == 0x7B


def test_parse_rejects_nonsense():
    with pytest.raises(ValueError):
        winhotkey.parse("Ctrl+Alt")
    with pytest.raises(ValueError):
        winhotkey.parse("Ctrl+Banana")
