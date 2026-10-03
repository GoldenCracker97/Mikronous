from pathlib import Path

from mikronous_cli import voice

SOUL = (Path(__file__).resolve().parent.parent / "profile" / "SOUL.md").read_text(encoding="utf-8")


def test_repo_soul_defaults_to_plain():
    assert voice.current(SOUL) == "plain"


def test_round_trip_keeps_rest_of_file():
    for level in voice.LEVELS:
        new = voice.set_level(SOUL, level)
        assert voice.current(new) == level
        assert new.split("<!-- voice:end -->", 1)[1] == SOUL.split("<!-- voice:end -->", 1)[1]
        assert new.startswith(SOUL.split("<!-- voice:start -->")[0])
    assert voice.set_level(voice.set_level(SOUL, "full"), "plain") == SOUL


def test_full_voice_keeps_hard_rules():
    text = voice.set_level(SOUL, "full")
    assert "one to three sentences" in text and "never shown" in text
