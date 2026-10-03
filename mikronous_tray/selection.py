"""Selected-text actions: grab what is highlighted in any app and run a fixed prompt on it.

Linux: the X11/Wayland PRIMARY selection through Qt (no extra tools). Windows has no primary selection,
so the tray sends Ctrl+C to the focused window, reads the clipboard and restores the old contents.
Prompts are plain so an 8B model follows them; *Rewrite* and *Translate* answers go back to the clipboard.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

MAX_CHARS = 12_000


@dataclass(frozen=True)
class Action:
    key: str
    label: str          # may contain {lang}
    prompt: str | None  # None = put the text in the input box and let the user ask
    copy_result: bool


ACTIONS: tuple[Action, ...] = (
    Action("explain", "Explain", "Explain the following clearly, in at most five sentences. If it is code, say what it does and "
                                 "point out anything risky.\n\n---\n{text}\n---", False),
    Action("summarize", "Summarise", "Summarise the following in at most five short bullet points. Keep names, numbers and dates "
                                     "exact.\n\n---\n{text}\n---", False),
    Action("rewrite", "Rewrite", "Rewrite the following so it is clear and well written, keeping the meaning, tone and language. "
                                 "Reply with only the rewritten text, nothing else.\n\n---\n{text}\n---", True),
    Action("translate", "Translate → {lang}", "Translate the following into {lang}. Reply with only the translation, "
                                               "nothing else.\n\n---\n{text}\n---", True),
    Action("ask", "Ask about it…", None, False),
)


def action(key: str) -> Action:
    for a in ACTIONS:
        if a.key == key:
            return a
    raise KeyError(key)


def build_prompt(key: str, text: str, lang: str = "English") -> str:
    a = action(key)
    text = text.strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rstrip() + "\n[… cut after 12,000 characters]"
    if a.prompt is None:
        return f'"""\n{text}\n"""\n'
    return a.prompt.format(text=text, lang=lang)


def label(a: Action, lang: str) -> str:
    return a.label.format(lang=lang)


# ----------------------------------------------------------------------------- grabbing
def grab_selection() -> str:
    """The currently highlighted text in whatever app has focus ('' when none)."""
    from PySide6.QtGui import QClipboard, QGuiApplication
    cb = QGuiApplication.clipboard()
    if sys.platform != "win32":
        text = cb.text(QClipboard.Selection) if cb.supportsSelection() else ""
        return text or ""
    return _grab_windows(cb)


def _grab_windows(cb) -> str:
    """Ctrl+C into the clipboard, read it, put the old clipboard back."""
    import ctypes
    from PySide6.QtCore import QCoreApplication
    old = cb.text()
    cb.setText("")
    VK_CONTROL, VK_C, KEYEVENTF_KEYUP = 0x11, 0x43, 0x0002
    user32 = ctypes.windll.user32
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 0, 0)
    user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    text = ""
    for _ in range(20):                       # up to ~1 s for the app to answer the copy
        time.sleep(0.05)
        QCoreApplication.processEvents()
        text = cb.text()
        if text:
            break
    cb.setText(old)
    return text or ""


def set_clipboard(text: str) -> None:
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().setText(text)
