"""The Machine Cult theme: palette tokens, bundled fonts, stylesheet, icons, and cant strings.

Always dark by design (it does not follow the KDE colour scheme). Everything visual in the tray
reads its colours from ``TOKENS`` so the theme lives in one file.
"""

from __future__ import annotations

import os
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"

TOKENS = {
    "bg": "#100d0c",          # iron-black ground
    "bg2": "#171210",         # plates / input
    "bg3": "#1b0e0d",         # sanction card fill
    "fg": "#e9dcc4",          # parchment text
    "muted": "#9b8c74",
    "line": "#3a2a22",
    "brass": "#c9962b",
    "brass_dim": "#7d5c17",
    "red": "#b3262b",         # Martian red
    "rust": "#8b1e1e",
    "red_line": "#5a1f1f",
    "user_bg": "#2a1413",
    "phosphor": "#7cf7a0",    # data-slate green
    "phosphor_dim": "#2f7a46",
}

# Font families after registration (fallbacks are what Qt uses when the TTFs failed to load).
FONT_CAPS = "Cinzel"
FONT_MONO = "Share Tech Mono"
FONT_GOTHIC = "Grenze Gotisch"
FALLBACK_CAPS = "serif"
FALLBACK_MONO = "monospace"

CANT = {
    "idle": "",
    "thinking": "++ COGITATING ++",
    "approval": "++ AWAITING SANCTION OF THE MAGISTER ++",
    "complete": "++ RITE COMPLETE ++",
    "interrupted": "++ RITE INTERRUPTED ++",
    "stopping": "++ CEASING ++",
    "stall": "++ THE MACHINE SPIRIT IS SLOW TO ANSWER · CEASE, OR BEGIN ANEW (CTRL+N) ++",
    "new": "++ NEW RITE BEGUN ++",
    "session": "++ RITE RESUMED ++",
    "model_waking": "++ THE COGITATOR WAKES · THE FIRST ANSWER MAY TAKE A MOMENT ++",
    "model_ready": "++ COGITATOR READY ++",
    "model_unloaded": "++ COGITATOR DORMANT · LOAD IT FROM THE TRAY MENU ++",
    "update_checking": "++ CONSULTING THE FORGE FOR NEW RITES ++",
    "update_current": "++ NO NEW RITES · THIS IS THE LATEST ++",
    "update_available": "++ NEW RITES AWAIT AT THE FORGE · PRESS UPDATE ++",
    "update_failed": "++ THE FORGE DID NOT ANSWER ++",
    "update_running": "++ REFORGING · THE SLATE WILL RETURN BY ITSELF ++",
    "settings_saved": "++ SETTINGS INSCRIBED ++",
    "gateway_restarting": "++ GATEWAY REFORGING · A MOMENT ++",
    "gateway_restarted": "++ SETTINGS INSCRIBED · GATEWAY BACK ONLINE ++",
    "session_opened": "++ RITE RECALLED ++",
    "attached": "++ {n} FILE(S) PLACED ON THE SLATE · ASK AWAY ++",
}

APPROVAL_LABELS = {"once": "Sanction once", "session": "This session", "always": "Always", "deny": "Refuse"}


def load_fonts() -> dict[str, str]:
    """Register the bundled OFL fonts; return the family name to use for each role."""
    from PySide6.QtGui import QFontDatabase
    families = {"caps": FALLBACK_CAPS, "mono": FALLBACK_MONO, "gothic": FALLBACK_CAPS}
    wanted = {"caps": ("Cinzel.ttf", FONT_CAPS), "mono": ("ShareTechMono-Regular.ttf", FONT_MONO),
              "gothic": ("GrenzeGotisch.ttf", FONT_GOTHIC)}
    for role, (fname, family) in wanted.items():
        path = ASSETS / "fonts" / fname
        if not path.is_file():
            continue
        try:
            fid = QFontDatabase.addApplicationFont(str(path))
            if fid >= 0:
                names = QFontDatabase.applicationFontFamilies(fid)
                families[role] = names[0] if names else family
        except Exception:  # noqa: BLE001 - keep the fallback
            pass
    return families


def stylesheet(f: dict[str, str]) -> str:
    t = TOKENS
    return f"""
    QWidget#chatRoot {{ background: {t['bg']}; color: {t['fg']}; }}
    QFrame#header {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2a1410, stop:1 #1a0d0b);
                     border-bottom: 1px solid {t['brass_dim']}; }}
    QLabel#title {{ font-family: "{f['caps']}"; font-size: 12px; letter-spacing: 2px; color: {t['brass']}; font-weight: 600; }}
    QLabel#model {{ font-family: "{f['mono']}"; font-size: 11px; color: {t['muted']}; }}
    QTextBrowser#view {{ background: {t['bg']}; color: {t['fg']}; border: none; padding: 4px; }}
    QLabel#status {{ font-family: "{f['mono']}"; font-size: 11px; color: {t['phosphor_dim']}; letter-spacing: 1px; }}
    QFrame#approval {{ background: {t['bg3']}; border: 1px solid {t['red']}; }}
    QLabel#approvalText {{ color: {t['fg']}; }}
    QFrame#approval QPushButton {{ font-family: "{f['caps']}"; font-size: 10px; letter-spacing: 1px; padding: 5px 11px;
                                   background: transparent; color: {t['brass']}; border: 1px solid {t['brass_dim']}; }}
    QFrame#approval QPushButton#primary {{ background: {t['brass']}; color: {t['bg']}; border-color: {t['brass']}; }}
    QFrame#approval QPushButton:hover {{ border-color: {t['brass']}; }}
    QPlainTextEdit#input {{ background: #0b0908; color: {t['fg']}; border: 1px solid {t['brass_dim']};
                            font-family: "{f['mono']}"; font-size: 13px; padding: 6px; selection-background-color: {t['rust']}; }}
    QPlainTextEdit#input:focus {{ border-color: {t['brass']}; }}
    QPushButton#send {{ font-family: "{f['caps']}"; font-size: 10px; letter-spacing: 1px; padding: 7px 12px;
                        background: {t['rust']}; color: #f1e6d2; border: 1px solid {t['red']}; }}
    QPushButton#send:hover {{ background: {t['red']}; }}
    QPushButton#send:disabled {{ background: #2a1413; color: {t['muted']}; border-color: {t['red_line']}; }}
    QPushButton#stop {{ font-family: "{f['caps']}"; font-size: 10px; letter-spacing: 1px; padding: 7px 12px;
                        background: transparent; color: {t['brass']}; border: 1px solid {t['brass_dim']}; }}
    QPushButton#stop:disabled {{ color: #4a3a2a; border-color: {t['line']}; }}
    QScrollBar:vertical {{ background: {t['bg']}; width: 8px; }}
    QScrollBar::handle:vertical {{ background: {t['brass_dim']}; min-height: 24px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QMenu {{ background: {t['bg2']}; color: {t['fg']}; border: 1px solid {t['brass_dim']}; }}
    QMenu::item:selected {{ background: {t['rust']}; }}
    QPushButton#hbtn {{ font-family: "{f['caps']}"; font-size: 9px; letter-spacing: 1px; padding: 3px 8px;
                        background: transparent; color: {t['brass']}; border: 1px solid {t['brass_dim']}; }}
    QPushButton#hbtn:hover {{ border-color: {t['brass']}; color: #f1e6d2; }}
    QPushButton#hbtn:checked {{ background: {t['brass_dim']}; color: {t['bg']}; }}
    QPushButton#hbtn[alert="true"] {{ color: {t['phosphor']}; border-color: {t['phosphor_dim']}; }}
    QFrame#sessions {{ background: {t['bg2']}; border-right: 1px solid {t['brass_dim']}; }}
    QLabel#paneTitle {{ font-family: "{f['caps']}"; font-size: 10px; letter-spacing: 2px; color: {t['brass']}; padding: 6px 8px 2px 8px; }}
    QListWidget#sessionList {{ background: {t['bg2']}; color: {t['fg']}; border: none; font-family: "{f['mono']}"; font-size: 12px; outline: 0; }}
    QListWidget#sessionList::item {{ padding: 6px 8px; border-bottom: 1px solid {t['line']}; }}
    QListWidget#sessionList::item:selected {{ background: {t['user_bg']}; color: #f1e6d2; border-left: 3px solid {t['red']}; }}
    QListWidget#sessionList::item:hover {{ background: #201714; }}
    QPushButton#paneNew {{ font-family: "{f['caps']}"; font-size: 9px; letter-spacing: 1px; padding: 5px 8px; margin: 6px 8px;
                           background: transparent; color: {t['brass']}; border: 1px solid {t['brass_dim']}; }}
    QPushButton#paneNew:hover {{ border-color: {t['brass']}; }}
    QDialog#settings {{ background: {t['bg']}; color: {t['fg']}; }}
    QDialog#settings QLabel {{ color: {t['fg']}; font-family: "{f['mono']}"; font-size: 12px; }}
    QDialog#settings QLabel#dlgTitle {{ font-family: "{f['caps']}"; font-size: 12px; letter-spacing: 2px; color: {t['brass']}; }}
    QDialog#settings QLabel#hint {{ color: {t['muted']}; font-size: 11px; }}
    QDialog#settings QComboBox, QDialog#settings QLineEdit {{ background: #0b0908; color: {t['fg']}; border: 1px solid {t['brass_dim']};
                           padding: 4px 6px; font-family: "{f['mono']}"; font-size: 12px; min-height: 18px; }}
    QDialog#settings QComboBox:focus, QDialog#settings QLineEdit:focus {{ border-color: {t['brass']}; }}
    QDialog#settings QComboBox::drop-down {{ border: none; width: 18px; }}
    QDialog#settings QComboBox QAbstractItemView {{ background: {t['bg2']}; color: {t['fg']}; selection-background-color: {t['rust']};
                           border: 1px solid {t['brass_dim']}; }}
    QDialog#settings QCheckBox {{ color: {t['fg']}; font-family: "{f['mono']}"; font-size: 12px; spacing: 8px; }}
    QDialog#settings QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {t['brass_dim']}; background: #0b0908; }}
    QDialog#settings QCheckBox::indicator:checked {{ background: {t['brass']}; }}
    QDialog#settings QPushButton {{ font-family: "{f['caps']}"; font-size: 10px; letter-spacing: 1px; padding: 6px 14px;
                           background: transparent; color: {t['brass']}; border: 1px solid {t['brass_dim']}; }}
    QDialog#settings QPushButton:hover {{ border-color: {t['brass']}; }}
    QDialog#settings QPushButton#primary {{ background: {t['rust']}; color: #f1e6d2; border-color: {t['red']}; }}
    QDialog#settings QPushButton#primary:hover {{ background: {t['red']}; }}
    QDialog#settings QFrame#rule {{ background: {t['line']}; max-height: 1px; min-height: 1px; border: none; }}
    QDialog#settings QTabWidget::pane {{ border: 1px solid {t['brass_dim']}; top: -1px; }}
    QDialog#settings QTabBar::tab {{ font-family: "{f['caps']}"; font-size: 10px; letter-spacing: 2px; padding: 6px 16px;
                           background: {t['bg2']}; color: {t['muted']}; border: 1px solid {t['brass_dim']}; border-bottom: none; margin-right: 2px; }}
    QDialog#settings QTabBar::tab:selected {{ background: {t['bg']}; color: {t['brass']}; }}
    QDialog#settings QPlainTextEdit {{ background: #0b0908; color: {t['fg']}; border: 1px solid {t['brass_dim']};
                           font-family: "{f['mono']}"; font-size: 12px; padding: 4px; }}
    QDialog#settings QListWidget#sessionList {{ border: 1px solid {t['brass_dim']}; }}
    QMessageBox {{ background: {t['bg']}; color: {t['fg']}; }}
    QMessageBox QLabel {{ color: {t['fg']}; }}
    QMessageBox QPushButton {{ font-family: "{f['caps']}"; font-size: 10px; letter-spacing: 1px; padding: 5px 12px;
                           background: transparent; color: {t['brass']}; border: 1px solid {t['brass_dim']}; }}
    QInputDialog {{ background: {t['bg']}; color: {t['fg']}; }}
    QInputDialog QLineEdit {{ background: #0b0908; color: {t['fg']}; border: 1px solid {t['brass_dim']}; padding: 4px; }}
    """


def transcript_css(f: dict[str, str]) -> str:
    """CSS for the QTextBrowser document (Qt supports a useful subset of CSS 2.1)."""
    t = TOKENS
    return f"""
    body {{ background: {t['bg']}; color: {t['fg']}; font-size: 13px; }}
    p {{ margin: 0 0 4px 0; }}
    .user {{ background: {t['user_bg']}; color: #f1e6d2; border-right: 3px solid {t['red']}; padding: 6px 10px; margin: 8px 0 4px 56px; }}
    .ai {{ font-family: "{f['mono']}", monospace; color: {t['phosphor']}; margin: 6px 56px 4px 0; }}
    .ai code, .ai pre {{ color: {t['fg']}; background: {t['bg2']}; }}
    .prompt {{ color: {t['phosphor_dim']}; }}
    .chip {{ font-family: "{f['mono']}", monospace; font-size: 11px; color: {t['muted']}; margin: 2px 0; }}
    .plate {{ border: 1px solid {t['brass_dim']}; background: {t['bg2']}; padding: 6px 10px; margin: 8px 0; }}
    .label {{ font-family: "{f['caps']}", serif; font-size: 10px; letter-spacing: 2px; color: {t['brass']}; }}
    .system {{ font-family: "{f['mono']}", monospace; font-size: 11px; color: {t['phosphor_dim']}; margin: 4px 0; }}
    .litany {{ font-family: "{f['gothic']}", serif; font-size: 15px; color: {t['brass']}; margin: 4px 0; }}
    a {{ color: {t['brass']}; }}
    """


def icon_path(kind: str = "color") -> Path:
    """'color' for the window and launcher, 'symbolic' for the tray (light glyph on a dark panel, or dark on light)."""
    if kind == "color":
        return ASSETS / "mikronous.svg"
    return ASSETS / ("mikronous-symbolic-dark.svg" if kind == "symbolic-dark" else "mikronous-symbolic-light.svg")


def qicon(kind: str = "color"):
    """A QIcon with real pixmaps at the usual sizes. An SVG-only QIcon reports no available sizes, so Qt
    exports nothing to the window manager and KWin shows a '?' on the title bar."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon
    src = QIcon(str(icon_path(kind)))
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64, 128, 256):
        pm = src.pixmap(QSize(size, size))
        if not pm.isNull():
            icon.addPixmap(pm)
    return icon if not icon.isNull() else src


def litany_lines(model: str, gateway_ok: bool) -> list[str]:
    return [
        "++ MACHINE SPIRIT AWAKENING ++",
        f"++ COGITATOR: {model or 'unknown'} ++",
        f"++ GATEWAY: {'ONLINE' if gateway_ok else 'UNREACHABLE'} ++",
        "++ THE MACHINE GOD PROVIDES ++",
    ]


def litany_enabled() -> bool:
    if os.environ.get("MIKRONOUS_NO_LITANY", "") in ("1", "true", "yes"):
        return False
    from . import settings
    return bool(settings.load().get("litany", True))
