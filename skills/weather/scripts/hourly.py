#!/usr/bin/env python3
"""Hourly weather for a place, from free keyless services. Prints one line per hour.

    hourly.py "Omaha, NE"              rest of today (local time at the place)
    hourly.py "Berlin" --hours 12      next 12 hours
    hourly.py "Omaha, NE" --units c    Celsius

US places use the National Weather Service API (api.weather.gov); everywhere else Open-Meteo.
Geocoding is OpenStreetMap's Nominatim. No account, no key. Only the place name leaves the machine.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

UA = "Mikronous/0.1 (local desktop assistant; https://github.com/GoldenCracker97/Mikronous)"


def get_json(url: str, timeout: float = 20.0) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json, application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - public weather APIs
        return json.loads(resp.read().decode("utf-8"))


def geocode(place: str) -> dict:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": place, "format": "json", "limit": 1, "addressdetails": 1})
    hits = get_json(url)
    if not hits:
        raise SystemExit(f"no place found for {place!r}")
    h = hits[0]
    return {"lat": float(h["lat"]), "lon": float(h["lon"]), "name": h.get("display_name", place),
            "country": (h.get("address") or {}).get("country_code", "").lower()}


def nws_hourly(lat: float, lon: float) -> list[dict]:
    points = get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    hourly_url = points["properties"]["forecastHourly"]
    data = get_json(hourly_url)
    out = []
    for p in data["properties"]["periods"]:
        pop = (p.get("probabilityOfPrecipitation") or {}).get("value")
        out.append({"time": datetime.fromisoformat(p["startTime"]), "temp": p["temperature"],
                    "unit": p.get("temperatureUnit", "F"), "text": p.get("shortForecast", ""),
                    "rain": pop, "wind": f'{p.get("windSpeed", "")} {p.get("windDirection", "")}'.strip()})
    return out


WMO = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
       51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
       66: "Freezing rain", 67: "Heavy freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
       80: "Rain showers", 81: "Showers", 82: "Violent showers", 85: "Snow showers", 86: "Heavy snow showers",
       95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, heavy hail"}


def open_meteo_hourly(lat: float, lon: float, units: str) -> list[dict]:
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "hourly": "temperature_2m,precipitation_probability,weather_code,wind_speed_10m",
        "forecast_days": 2, "timezone": "auto", "temperature_unit": "fahrenheit" if units == "f" else "celsius",
        "wind_speed_unit": "mph" if units == "f" else "kmh"})
    d = get_json(url)
    h = d["hourly"]
    out = []
    for t, temp, pop, code, wind in zip(h["time"], h["temperature_2m"], h["precipitation_probability"], h["weather_code"], h["wind_speed_10m"]):
        out.append({"time": datetime.fromisoformat(t), "temp": round(temp), "unit": "F" if units == "f" else "C",
                    "text": WMO.get(code, f"code {code}"), "rain": pop, "wind": f"{round(wind)} {'mph' if units == 'f' else 'km/h'}"})
    return out, d.get("utc_offset_seconds", 0)


def convert(rows: list[dict], units: str) -> None:
    for r in rows:
        if units == "c" and r["unit"] == "F":
            r["temp"], r["unit"] = round((r["temp"] - 32) * 5 / 9), "C"
        elif units == "f" and r["unit"] == "C":
            r["temp"], r["unit"] = round(r["temp"] * 9 / 5 + 32), "F"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="hourly weather, free and keyless")
    ap.add_argument("place")
    ap.add_argument("--hours", type=int, default=0, help="number of hours (default: rest of today)")
    ap.add_argument("--units", choices=["f", "c"], default="f")
    a = ap.parse_args(argv)
    try:
        g = geocode(a.place)
        if g["country"] == "us":
            rows = nws_hourly(g["lat"], g["lon"])
            source = "National Weather Service"
            now_local = rows[0]["time"] if rows else datetime.now().astimezone()
        else:
            rows, offset = open_meteo_hourly(g["lat"], g["lon"], a.units)
            source = "Open-Meteo"
            now_local = (datetime.utcnow() + timedelta(seconds=offset)).replace(minute=0, second=0, microsecond=0)
            rows = [r for r in rows if r["time"] >= now_local]
        convert(rows, a.units)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the model needs a readable reason, not a traceback
        print(f"weather lookup failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    if a.hours > 0:
        rows = rows[: a.hours]
    else:
        end_of_day = now_local.replace(hour=23, minute=59)
        rows = [r for r in rows if r["time"].replace(tzinfo=None) <= end_of_day.replace(tzinfo=None)] or rows[:12]
    print(f"{g['name']}  ({source})")
    for r in rows:
        rain = f"rain {r['rain']}%" if r["rain"] is not None else ""
        print(f"{r['time'].strftime('%H:%M')}  {r['temp']:>3}°{r['unit']}  {r['text']:<24} {rain:<9} {r['wind']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
