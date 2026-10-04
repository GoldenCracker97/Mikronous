"""Media and system control: play/pause, volume, brightness, do-not-disturb, lock, focus a window.

Linux: MPRIS over D-Bus (``playerctl`` when installed, ``gdbus`` otherwise), PipeWire ``wpctl`` or
PulseAudio ``pactl``, KDE's brightness D-Bus service (``brightnessctl`` fallback), Plasma's
``plasmanotifyrc`` DND timer, ``loginctl``, ``kdotool``/``xdotool``.  Windows: media and volume keys via
PowerShell SendKeys, WMI brightness, LockWorkStation, AppActivate.  Everything returns a dict and never
raises on a missing tool; levels are clamped to 0-100.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
import subprocess
import sys
from typing import Any

from .desktop import _PS, _env

IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MPRIS_PREFIX = "org.mpris.MediaPlayer2."
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"


def _sh(cmd: list[str], timeout: float = 8.0, input_text: str | None = None) -> tuple[int, str]:
    """(returncode, stdout+stderr). Monkeypatched in tests."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_env(), input=input_text,
                           creationflags=_NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def _has(tool: str) -> bool:
    return shutil.which(tool) is not None


def _ps(script: str) -> tuple[int, str]:
    return _sh(_PS + [script], timeout=15)


def _clamp(value: Any, lo: int = 0, hi: int = 100) -> int | None:
    try:
        v = int(round(float(str(value).rstrip("%"))))
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, v))


# ----------------------------------------------------------------------------- media (MPRIS)
def mpris_players(list_names_output: str) -> list[str]:
    """Player short names from ``ListNames`` output (gdbus GVariant text or one name per line)."""
    return sorted({m.group(1) for m in re.finditer(r"org\.mpris\.MediaPlayer2\.([\w.\-]+)", list_names_output)})


def _players() -> list[str]:
    if _has("playerctl"):
        rc, out = _sh(["playerctl", "-l"])
        return [p.strip() for p in out.splitlines() if p.strip() and "No players" not in p] if rc == 0 else []
    rc, out = _sh(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus",
                   "--method", "org.freedesktop.DBus.ListNames"])
    return mpris_players(out) if rc == 0 else []


def _mpris_call(player: str, method: str) -> tuple[int, str]:
    return _sh(["gdbus", "call", "--session", "--dest", MPRIS_PREFIX + player, "--object-path", "/org/mpris/MediaPlayer2",
                "--method", f"{PLAYER_IFACE}.{method}"])


def _mpris_prop(player: str, prop: str) -> str:
    rc, out = _sh(["gdbus", "call", "--session", "--dest", MPRIS_PREFIX + player, "--object-path", "/org/mpris/MediaPlayer2",
                   "--method", "org.freedesktop.DBus.Properties.Get", PLAYER_IFACE, prop])
    return out if rc == 0 else ""


def parse_metadata(gvariant: str) -> dict:
    """title/artist/status out of gdbus's GVariant text (good enough for a status line)."""
    out = {}
    m = re.search(r"'xesam:title':\s*<'((?:[^'\\]|\\.)*)'>", gvariant)
    if m:
        out["title"] = m.group(1).replace("\\'", "'")
    m = re.search(r"'xesam:artist':\s*<\['((?:[^'\\]|\\.)*)'", gvariant)
    if m:
        out["artist"] = m.group(1).replace("\\'", "'")
    m = re.search(r"<'(Playing|Paused|Stopped)'>", gvariant)
    if m:
        out["status"] = m.group(1).lower()
    return out


def media_control(args: dict, **_: Any) -> dict:
    action = str(args.get("action") or "status").lower()
    want = str(args.get("player") or "").lower()
    if action not in ("play", "pause", "toggle", "stop", "next", "previous", "status"):
        return {"error": "action must be one of play, pause, toggle, stop, next, previous, status"}
    if IS_WINDOWS:
        vk = {"toggle": 0xB3, "play": 0xB3, "pause": 0xB3, "stop": 0xB2, "next": 0xB0, "previous": 0xB1}.get(action)
        if vk is None:
            return {"error": "status is not available on Windows; use play/pause/next/previous"}
        err = _win_key(vk)
        return {"ok": True, "action": action, "via": "media key"} if not err else {"error": err}
    players = _players()
    if not players:
        return {"error": "no media player is running (nothing on MPRIS)"}
    player = next((p for p in players if want and want in p.lower()), None) if want else None
    if want and player is None:
        return {"error": f"no running player matches '{want}'", "players": players}
    if player is None:
        # prefer whichever is playing, else the first
        for p in players:
            if "Playing" in _mpris_prop(p, "PlaybackStatus"):
                player = p
                break
        player = player or players[0]
    if action == "status":
        info = parse_metadata(_mpris_prop(player, "Metadata") + " " + _mpris_prop(player, "PlaybackStatus"))
        return {"player": player, **info, "players": players}
    if _has("playerctl"):
        verb = {"toggle": "play-pause", "previous": "previous"}.get(action, action)
        rc, out = _sh(["playerctl", "-p", player, verb])
    else:
        method = {"play": "Play", "pause": "Pause", "toggle": "PlayPause", "stop": "Stop", "next": "Next", "previous": "Previous"}[action]
        rc, out = _mpris_call(player, method)
    if rc != 0:
        return {"error": f"{player}: {out.strip()[:200]}"}
    return {"ok": True, "player": player, "action": action}


# ----------------------------------------------------------------------------- volume
def parse_volume(text: str) -> tuple[int | None, bool]:
    """(percent, muted) from ``wpctl get-volume`` ("Volume: 0.45 [MUTED]") or ``pactl`` ("... 45% ...")."""
    m = re.search(r"Volume:\s*([0-9.]+)", text)
    if m:
        return _clamp(float(m.group(1)) * 100), "[MUTED]" in text
    m = re.search(r"(\d+)%", text)
    if m:
        return _clamp(m.group(1)), bool(re.search(r"Mute:\s*yes", text))
    return None, False


def _volume_get() -> dict:
    if _has("wpctl"):
        rc, out = _sh(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        if rc == 0:
            pct, muted = parse_volume(out)
            return {"volume": pct, "muted": muted, "via": "wpctl"}
    if _has("pactl"):
        rc, out = _sh(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        rc2, mute = _sh(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        if rc == 0:
            pct, _ = parse_volume(out)
            return {"volume": pct, "muted": "yes" in mute, "via": "pactl"}
    return {"error": "no audio control found (wpctl or pactl)"}


def _volume_set(pct: int) -> dict:
    if _has("wpctl"):
        rc, out = _sh(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{pct / 100:.2f}"])
    elif _has("pactl"):
        rc, out = _sh(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"])
    else:
        return {"error": "no audio control found (wpctl or pactl)"}
    return {"ok": True, "volume": pct} if rc == 0 else {"error": out.strip()[:200]}


def _mute(state: str) -> dict:   # "1" | "0" | "toggle"
    if _has("wpctl"):
        rc, out = _sh(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", state])
    elif _has("pactl"):
        rc, out = _sh(["pactl", "set-sink-mute", "@DEFAULT_SINK@", {"1": "1", "0": "0"}.get(state, "toggle")])
    else:
        return {"error": "no audio control found (wpctl or pactl)"}
    return {"ok": True, "muted": {"1": True, "0": False}.get(state)} if rc == 0 else {"error": out.strip()[:200]}


def _win_key(vk: int, times: int = 1) -> str:
    """Press a virtual key (media/volume keys) through user32; '' on success, else the error."""
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        for _ in range(times):
            user32.keybd_event(vk, 0, 0x0001, 0)            # KEYEVENTF_EXTENDEDKEY
            user32.keybd_event(vk, 0, 0x0003, 0)            # ... | KEYEVENTF_KEYUP
        return ""
    except Exception as exc:  # noqa: BLE001
        return f"could not press the key: {exc}"


def _volume_windows(action: str, pct: int | None) -> dict:
    # Each volume key press is one step (2 %). No read-back without the audio COM API.
    if action == "mute":
        err = _win_key(0xAD)
        return {"ok": True, "note": "mute toggled (Windows cannot report the state here)"} if not err else {"error": err}
    if action in ("volume_up", "volume_down"):
        err = _win_key(0xAF if action == "volume_up" else 0xAE, 5)
        return {"ok": True, "step": "10%"} if not err else {"error": err}
    if action == "volume_set" and pct is not None:
        err = _win_key(0xAE, 50) or _win_key(0xAF, pct // 2)
        return {"ok": True, "volume": pct - pct % 2} if not err else {"error": err}
    return {"error": "reading the volume is not supported on Windows; set it instead"}


# ----------------------------------------------------------------------------- brightness
_BR_DEST = ["--dest", "org.kde.Solid.PowerManagement", "--object-path", "/org/kde/Solid/PowerManagement/Actions/BrightnessControl"]
_BR_IFACE = "org.kde.Solid.PowerManagement.Actions.BrightnessControl."


def _gd_int(out: str) -> int | None:
    m = re.search(r"\(?(?:int\d*\s+)?(-?\d+),?\)?", out)
    return int(m.group(1)) if m else None


def _brightness_get() -> dict:
    if IS_WINDOWS:
        rc, out = _ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness")
        v = _clamp(out.strip().splitlines()[0]) if rc == 0 and out.strip() else None
        return {"brightness": v, "via": "wmi"} if v is not None else {"error": "no controllable display (desktop monitors usually are not)"}
    if _has("gdbus"):
        rc, cur = _sh(["gdbus", "call", "--session", *_BR_DEST, "--method", _BR_IFACE + "brightness"])
        rc2, mx = _sh(["gdbus", "call", "--session", *_BR_DEST, "--method", _BR_IFACE + "brightnessMax"])
        c, m = _gd_int(cur), _gd_int(mx)
        if rc == 0 and rc2 == 0 and c is not None and m:
            return {"brightness": round(100 * c / m), "via": "kde"}
    if _has("brightnessctl"):
        rc, cur = _sh(["brightnessctl", "g"])
        rc2, mx = _sh(["brightnessctl", "m"])
        if rc == 0 and rc2 == 0 and cur.strip().isdigit() and mx.strip().isdigit() and int(mx) > 0:
            return {"brightness": round(100 * int(cur) / int(mx)), "via": "brightnessctl"}
    return {"error": "no brightness control found (a laptop panel is needed; desktop monitors usually have none)"}


def _brightness_set(pct: int) -> dict:
    if IS_WINDOWS:
        rc, out = _ps(f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods).WmiSetBrightness(1,{pct})")
        return {"ok": True, "brightness": pct} if rc == 0 else {"error": out.strip()[:200]}
    if _has("gdbus"):
        rc2, mx = _sh(["gdbus", "call", "--session", *_BR_DEST, "--method", _BR_IFACE + "brightnessMax"])
        m = _gd_int(mx)
        if rc2 == 0 and m:
            rc, out = _sh(["gdbus", "call", "--session", *_BR_DEST, "--method", _BR_IFACE + "setBrightness", str(round(m * pct / 100))])
            if rc == 0:
                return {"ok": True, "brightness": pct, "via": "kde"}
    if _has("brightnessctl"):
        rc, out = _sh(["brightnessctl", "s", f"{pct}%"])
        return {"ok": True, "brightness": pct, "via": "brightnessctl"} if rc == 0 else {"error": out.strip()[:200]}
    return {"error": "no brightness control found"}


# ----------------------------------------------------------------------------- do not disturb, lock, focus
def _kwriteconfig() -> str | None:
    return next((t for t in ("kwriteconfig6", "kwriteconfig5") if _has(t)), None)


def _dnd(on: bool, minutes: int) -> dict:
    if IS_WINDOWS:
        return {"error": "Focus Assist has no command-line switch; toggle it from the Action Center"}
    kw = _kwriteconfig()
    if not kw:
        return {"error": "kwriteconfig not found (Plasma's do-not-disturb timer lives in plasmanotifyrc)"}
    if on:
        until = _dt.datetime.now() + _dt.timedelta(minutes=minutes)
        value = f"{until.year},{until.month},{until.day},{until.hour},{until.minute},{until.second}"   # KConfig QDateTime
        rc, out = _kwrite_notify(kw, ["--file", "plasmanotifyrc", "--group", "DoNotDisturb", "--key", "Until", value])
        return {"ok": True, "until": until.strftime("%H:%M")} if rc == 0 else {"error": out.strip()[:200]}
    rc, out = _kwrite_notify(kw, ["--file", "plasmanotifyrc", "--group", "DoNotDisturb", "--key", "Until", "--delete"])
    return {"ok": True, "dnd": False} if rc == 0 else {"error": out.strip()[:200]}


def _kwrite_notify(kw: str, args: list[str]) -> tuple[int, str]:
    """kwriteconfig with --notify (Plasma only reloads plasmanotifyrc on a change notice); older builds lack the flag."""
    rc, out = _sh([kw, "--notify", *args])
    if rc != 0 and "notify" in out.lower():
        rc, out = _sh([kw, *args])
    return rc, out


def _lock() -> dict:
    if IS_WINDOWS:
        rc, out = _sh(["rundll32.exe", "user32.dll,LockWorkStation"])
        return {"ok": rc == 0} if rc == 0 else {"error": out.strip()[:200]}
    for cmd in (["loginctl", "lock-session"],
                ["qdbus6", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"],
                ["qdbus", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"],
                ["xdg-screensaver", "lock"]):
        if _has(cmd[0]):
            rc, out = _sh(cmd)
            if rc == 0:
                return {"ok": True, "via": cmd[0]}
    return {"error": "no screen locker found (loginctl / qdbus / xdg-screensaver)"}


def _focus(name: str) -> dict:
    if not name:
        return {"error": "window is required (part of its title or app name)"}
    if IS_WINDOWS:
        rc, out = _ps(f"(New-Object -ComObject WScript.Shell).AppActivate('{name.replace(chr(39), chr(39)*2)}')")
        return {"ok": "True" in out} if rc == 0 else {"error": out.strip()[:200]}
    if _has("kdotool"):
        rc, out = _sh(["kdotool", "search", "--name", name])
        ids = [l.strip() for l in out.splitlines() if l.strip()]
        if rc == 0 and ids:
            rc, out = _sh(["kdotool", "windowactivate", ids[0]])
            return {"ok": rc == 0, "matches": len(ids), "via": "kdotool"}
    if _has("xdotool"):
        rc, out = _sh(["xdotool", "search", "--name", name])
        ids = [l.strip() for l in out.splitlines() if l.strip()]
        if rc == 0 and ids:
            rc, out = _sh(["xdotool", "windowactivate", "--sync", ids[-1]])
            return {"ok": rc == 0, "matches": len(ids), "via": "xdotool"}
    return {"error": f"no window matching '{name}' (or kdotool/xdotool not installed)"}


SYSTEM_ACTIONS = ("volume_get", "volume_set", "volume_up", "volume_down", "mute", "unmute", "brightness_get", "brightness_set",
                  "dnd_on", "dnd_off", "lock", "focus_window")


def system_control(args: dict, **_: Any) -> dict:
    action = str(args.get("action") or "").lower()
    if action not in SYSTEM_ACTIONS:
        return {"error": f"action must be one of {', '.join(SYSTEM_ACTIONS)}"}
    value = args.get("value")
    if action == "volume_get":
        return _volume_windows(action, None) if IS_WINDOWS else _volume_get()
    if action == "volume_set":
        pct = _clamp(value)
        if pct is None:
            return {"error": "value must be a level 0-100"}
        return _volume_windows(action, pct) if IS_WINDOWS else _volume_set(pct)
    if action in ("volume_up", "volume_down"):
        if IS_WINDOWS:
            return _volume_windows(action, None)
        cur = _volume_get()
        if "error" in cur or cur.get("volume") is None:
            return cur if "error" in cur else {"error": "could not read the current volume"}
        step = (_clamp(value) if value is not None else None) or 10
        return _volume_set(_clamp(cur["volume"] + (step if action == "volume_up" else -step)))
    if action in ("mute", "unmute"):
        if IS_WINDOWS:
            res = _volume_windows("mute", None)
            if action == "unmute" and "ok" in res:
                res["note"] = "Windows only has a mute toggle here: pressed it once; check the speaker icon"
            return res
        return _mute("1" if action == "mute" else "0")
    if action == "brightness_get":
        return _brightness_get()
    if action == "brightness_set":
        pct = _clamp(value)
        return {"error": "value must be a level 0-100"} if pct is None else _brightness_set(pct)
    if action in ("dnd_on", "dnd_off"):
        minutes = _clamp(value, 1, 24 * 60) if value is not None else 60
        return _dnd(action == "dnd_on", minutes or 60)
    if action == "lock":
        return _lock()
    return _focus(str(args.get("window") or value or "").strip())
