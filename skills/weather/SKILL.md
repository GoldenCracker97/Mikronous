---
name: weather
description: Hourly or daily weather for any place, from free keyless services, without the browser.
version: 1.0.0
author: Mikronous
license: MIT
platforms: [linux, windows]
metadata:
  hermes:
    tags: [weather, forecast, web]
    requires_toolsets: [terminal]
---

# Weather

## When to use
The user asks about the weather, a forecast, temperature, rain, wind, or whether to bring a coat, for any place.

## Procedure
1. Run the bundled script with the `terminal` tool. It geocodes the place and reads the National Weather
   Service API (US) or Open-Meteo (elsewhere); both are free, keyless, and return plain data.
   - Linux: `python3 ~/.hermes/profiles/mikronous/skills/mikronous/weather/scripts/hourly.py "<place>"`
   - Windows: `python "%LOCALAPPDATA%\hermes\profiles\mikronous\skills\mikronous\weather\scripts\hourly.py" "<place>"`
   - Options: `--hours 12` for the next 12 hours (default is the rest of today), `--units c` for Celsius.
2. Relay the output as a compact list, one line per hour: time, temperature, condition, rain chance. Keep
   the script's times; they are local to the place.
3. Mention anything that matters (fog, storms, a big temperature swing) in one sentence. Name the source.

## Pitfalls
- Never use the browser or `web_extract` on weather websites: they are built with JavaScript and give no usable text.
- If the script prints `no place found`, ask for a more specific place (city plus state or country).
- If it prints `weather lookup failed`, say so in one line; do not try to scrape a site instead.
