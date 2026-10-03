---
name: desktop-control
description: Play/pause and skip media, set volume and brightness, do-not-disturb, lock the screen, focus a window.
version: 1.0.0
author: Mikronous
license: MIT
platforms: [linux, windows]
metadata:
  hermes:
    tags: [desktop, media, volume, brightness, kde]
    requires_toolsets: [mikronous]
---

# Desktop control

## When to use
"Pause the music", "next track", "what's playing?", "volume 30", "louder", "mute", "dim the screen", "don't disturb me for an hour", "lock the screen", "bring up Firefox".

## Procedure
- **Media**: `media_control` with `toggle` for "pause"/"play" when you do not know the state, `next`/`previous` to skip, `status` for "what's playing". Name the `player` only when the user does (spotify, vlc, firefox). Answer in one line: what happened, and the track when known.
- **Volume**: "volume 30" → `system_control` `volume_set` 30. "louder"/"quieter" → `volume_up`/`volume_down` (default step 10). "mute"/"unmute" are their own actions. Report the resulting level.
- **Brightness**: `brightness_get` / `brightness_set` (0-100). Desktop monitors usually cannot be controlled; say so when the tool says there is no controllable display.
- **Do not disturb**: `dnd_on` with `value` = minutes (default 60), `dnd_off` to end it early. Tell the user when it ends.
- **Lock**: `lock`. No confirmation needed; it is harmless.
- **Focus a window**: `focus_window` with `window` = part of the title or app name ("Firefox", "Dolphin").

## Pitfalls
- There is no suspend, reboot or shutdown action on purpose; if asked, say it is not something you do and suggest the power menu.
- One action per call; "pause and mute" is two calls.
- On Windows, volume cannot be read back and do-not-disturb is not scriptable; the tool says so.
