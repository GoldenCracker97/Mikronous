import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "weather" / "scripts" / "hourly.py"
spec = importlib.util.spec_from_file_location("hourly", SCRIPT)
hourly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hourly)


def _fake_get_json(responses):
    def f(url, timeout=20.0):
        for key, value in responses.items():
            if key in url:
                return value
        raise AssertionError(f"unexpected url {url}")
    return f


def test_us_path_uses_nws(monkeypatch, capsys):
    now = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
    periods = [{"startTime": (now.replace(hour=14 + i)).isoformat(), "temperature": 64 + i, "temperatureUnit": "F",
                "shortForecast": "Pleasant", "probabilityOfPrecipitation": {"value": 10 * i}, "windSpeed": "5 mph",
                "windDirection": "S"} for i in range(4)]
    monkeypatch.setattr(hourly, "get_json", _fake_get_json({
        "nominatim": [{"lat": "41.25", "lon": "-95.93", "display_name": "Omaha, Nebraska, USA", "address": {"country_code": "us"}}],
        "api.weather.gov/points": {"properties": {"forecastHourly": "https://api.weather.gov/gridpoints/OAX/1,2/forecast/hourly"}},
        "forecast/hourly": {"properties": {"periods": periods}},
    }))
    assert hourly.main(["Omaha, NE"]) == 0
    out = capsys.readouterr().out
    assert "National Weather Service" in out and "14:00   64°F" in out and "rain 10%" in out
    assert out.count("°F") == 4


def test_non_us_path_uses_open_meteo_and_celsius(monkeypatch, capsys):
    class FixedNow(datetime):                       # the script filters "from now on" in the place's local time
        @classmethod
        def utcnow(cls):
            return cls(2026, 10, 3, 10, 0)          # 12:00 in Berlin (UTC+2 in the fake payload)
    monkeypatch.setattr(hourly, "datetime", FixedNow)
    times = [f"2026-10-03T{h:02d}:00" for h in range(24)]
    monkeypatch.setattr(hourly, "get_json", _fake_get_json({
        "nominatim": [{"lat": "52.52", "lon": "13.40", "display_name": "Berlin, Deutschland", "address": {"country_code": "de"}}],
        "open-meteo": {"utc_offset_seconds": 7200, "hourly": {"time": times, "temperature_2m": [10 + h for h in range(24)],
                       "precipitation_probability": [h for h in range(24)], "weather_code": [3] * 24, "wind_speed_10m": [12] * 24}},
    }))
    assert hourly.main(["Berlin", "--hours", "3", "--units", "c"]) == 0
    out = capsys.readouterr().out
    assert "Open-Meteo" in out and "Overcast" in out and "°C" in out and out.count("\n") == 4
    assert "12:00   22°C" in out


def test_unknown_place(monkeypatch):
    monkeypatch.setattr(hourly, "get_json", _fake_get_json({"nominatim": []}))
    import pytest
    with pytest.raises(SystemExit):
        hourly.main(["Nowhereville Zzz"])
