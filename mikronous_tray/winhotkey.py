"""Global hotkey on Windows via RegisterHotKey, delivered to Qt through a native event filter."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x4D49  # "MI": the toggle; further hotkeys use HOTKEY_ID + n
_VK = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
       "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E, "pause": 0x13}


def parse(spec: str) -> tuple[int, int]:
    mods, vk = MOD_NOREPEAT, 0
    for part in [p.strip().lower() for p in spec.split("+") if p.strip()]:
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "meta", "super"):
            mods |= MOD_WIN
        elif part in _VK:
            vk = _VK[part]
        elif len(part) == 1:
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            vk = 0x70 + int(part[1:]) - 1
        else:
            raise ValueError(f"unknown key '{part}' in hotkey '{spec}'")
    if not vk:
        raise ValueError(f"hotkey '{spec}' has no key")
    return mods, vk


class WinHotkey(QAbstractNativeEventFilter):
    def __init__(self, spec: str, callback, app, hotkey_id: int = HOTKEY_ID):
        super().__init__()
        self.spec, self.callback, self.ok, self.error = spec, callback, False, ""
        self.hotkey_id = hotkey_id
        if sys.platform != "win32":
            self.error = "not Windows"
            return
        try:
            mods, vk = parse(spec)
        except ValueError as exc:
            self.error = str(exc)
            return
        self.ok = bool(ctypes.windll.user32.RegisterHotKey(None, self.hotkey_id, mods, vk))
        if not self.ok:
            self.error = f"RegisterHotKey failed (code {ctypes.GetLastError()}); is the key already taken?"
            return
        app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                self.callback()
                return True, 0
        return False, 0

    def unregister(self) -> None:
        if self.ok and sys.platform == "win32":
            ctypes.windll.user32.UnregisterHotKey(None, self.hotkey_id)
            self.ok = False
        if sys.platform == "win32" and self.ok is False:
            try:
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                if app is not None:
                    app.removeNativeEventFilter(self)
            except Exception:  # noqa: BLE001
                pass
