---
name: home-assistant
description: Read sensors and control lights, switches, scenes and media through the user's Home Assistant.
version: 1.0.0
author: Mikronous
license: MIT
platforms: [linux, windows]
metadata:
  hermes:
    tags: [smart-home, home-assistant]
    requires_toolsets: [homeassistant]
---

# Home Assistant

## Setup (once)
The user puts two lines in the Mikronous profile `.env` (`~/.hermes/profiles/mikronous/.env`, or
`%LOCALAPPDATA%\hermes\profiles\mikronous\.env` on Windows) and restarts the gateway (`mik update` or
`hermes -p mikronous gateway restart`):
```
HASS_URL=http://homeassistant.local:8123
HASS_TOKEN=<a long-lived access token from their Home Assistant profile page>
```
`mik doctor` then shows a `home assistant` row. If the tools below are missing, that is what is missing.

## Procedure
1. Find the entity: `ha_list_entities` (filter by domain such as `light`, `switch`, `climate`, `media_player`, `sensor`) and match the friendly name the user used. Remember the `entity_id` for the session.
2. Read: `ha_get_state` for the current state and attributes (brightness, temperature, battery).
3. Act: `ha_call_service` with the domain/service and the entity, e.g. `light.turn_on` with `brightness_pct`, `switch.turn_off`, `scene.turn_on`, `climate.set_temperature`, `media_player.media_pause`. Confirm in one line what changed.
4. Anything that affects safety or the whole house (alarm, locks, garage, heating off in winter) needs an explicit yes from the user first.

## Pitfalls
- Several entities match? Ask which, listing the friendly names.
- Never change automations or configuration; only call services.
