import pytest

from mikronous.reminders import extract_when_and_message, normalize_when


@pytest.mark.parametrize("args, expected", [
    ({"message": "Remind me in 1 minute to blink"}, ("blink", "in 1 minute")),
    ({"message": "Blink", "when": "in 1 minute"}, ("Blink", "in 1 minute")),
    ({"text": "stretch", "time": "in 2 minutes"}, ("stretch", "in 2 minutes")),
    ({"message": "call mom tomorrow at 9am"}, ("call mom", "tomorrow at 9am")),
    ({"message": "drink water", "in": 5}, ("drink water", "in 5 minutes")),
    ({"message": "stand up every 30 minutes"}, ("stand up", "every 30 minutes")),
    ({"message": "take pills every weekday at 9am"}, ("take pills", "every weekday at 9am")),
    ({"message": "Stretch"}, ("Stretch", "")),
])
def test_extract_when_and_message(args, expected):
    assert extract_when_and_message(args) == expected


@pytest.mark.parametrize("when, expected", [
    ("in 2 minutes", "in 2m"), ("in 1.5 hours", "in 90m"), ("in an hour", "in 60m"),
    ("every 30 minutes", "every 30m"), ("every weekday at 9am", "every weekday at 9am"),
])
def test_normalize_when(when, expected):
    assert normalize_when(when) == expected


def test_normalize_when_clock_time_is_iso():
    out = normalize_when("tomorrow at 9am")
    assert out[:4].isdigit() and "T09:00" in out
