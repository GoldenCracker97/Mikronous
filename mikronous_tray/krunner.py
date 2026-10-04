"""KRunner integration (Linux): type ``mik <question>`` in KRunner (Alt+Space) and the answer arrives
without opening the window.

The tray process serves ``org.kde.krunner1`` on the session bus (service ``org.mikronous.Runner``,
path ``/runner``) with ``dbus-fast`` on its own asyncio thread; KRunner finds it through
``~/.local/share/krunner/dbusplugins/plasma-runner-mikronous.desktop`` (written by the installer).
``Match`` is pure and runs on the D-Bus thread; ``Run`` hands the match id to the Qt thread via a signal.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

SERVICE = "org.mikronous.Runner"
PATH = "/runner"
PREFIX = re.compile(r"^\s*mik(?:ronous)?\b[:,]?\s*(.*)$", re.IGNORECASE | re.DOTALL)
EXACT, HELPER, INFORMATIONAL = 100, 70, 50
COMMANDS = {                       # keyword → (match id, text)
    "settings": ("cmd:settings", "Mikronous: open Settings"),
    "routines": ("cmd:routines", "Mikronous: open Routines"),
    "chats": ("cmd:chats", "Mikronous: show past chats"),
    "new": ("cmd:new", "Mikronous: start a new chat"),
    "show": ("cmd:show", "Mikronous: show the window"),
    "update": ("cmd:update", "Mikronous: check for updates"),
}

DESKTOP_FILE = Path("~/.local/share/krunner/dbusplugins/plasma-runner-mikronous.desktop").expanduser()
DESKTOP_ENTRY = f"""[Desktop Entry]
Name=Mikronous
Comment=Ask the local assistant: type mik and a question
Icon=mikronous
Type=Service
X-KDE-ServiceTypes=Plasma/Runner
X-Plasma-API=DBus
X-Plasma-DBusRunner-Service={SERVICE}
X-Plasma-DBusRunner-Path={PATH}
X-Plasma-Runner-Match-Regex=^\\s*mik(ronous)?\\b
X-Plasma-Runner-Min-Letter-Count=4
X-Plasma-Request-Actions-Once=true
X-KDE-PluginInfo-Name=mikronous
X-KDE-PluginInfo-Author=Mikronous
X-KDE-PluginInfo-Version=1.0
X-KDE-PluginInfo-License=MIT
X-KDE-PluginInfo-EnabledByDefault=true
"""


def parse_query(query: str) -> str | None:
    """The question after the ``mik`` prefix, or None when the query is not for us."""
    m = PREFIX.match(query or "")
    return m.group(1).strip() if m else None


def build_matches(query: str) -> list[tuple[str, str, str, int, float, dict]]:
    """KRunner matches as (id, text, icon, type, relevance, properties) with plain-Python properties."""
    q = parse_query(query)
    if q is None:
        return []
    out = []
    if q:
        out.append((f"ask:{q}", f"Ask Mikronous: {q}", "mikronous", EXACT, 1.0,
                    {"subtext": "answers on the slate; a notification when the window is hidden"}))
    for word, (mid, text) in COMMANDS.items():
        if not q or word.startswith(q.lower()) or q.lower() == word:
            out.append((mid, text, "mikronous", HELPER if q else INFORMATIONAL, 0.6, {"subtext": "command"}))
    return out


def install_desktop_file() -> Path:
    DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DESKTOP_FILE.exists() or DESKTOP_FILE.read_text(encoding="utf-8") != DESKTOP_ENTRY:
        DESKTOP_FILE.write_text(DESKTOP_ENTRY, encoding="utf-8")
    return DESKTOP_FILE


# ----------------------------------------------------------------------------- the D-Bus service
class KRunnerService:
    """Runs the ``org.kde.krunner1`` interface on a daemon thread. ``on_run(match_id)`` is called from that
    thread, so pass something thread-safe (a Qt signal's emit)."""

    def __init__(self, on_run):
        self.on_run = on_run
        self.error = ""
        self.ok = False
        self._thread: threading.Thread | None = None
        self._loop = None
        self._bus = None

    def start(self) -> bool:
        try:
            import dbus_fast  # noqa: F401
        except ImportError:
            self.error = "dbus-fast is not installed (pip install dbus-fast)"
            return False
        self._thread = threading.Thread(target=self._serve, name="mikronous-krunner", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        loop, bus = self._loop, self._bus
        if loop and bus and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(bus.disconnect)
            except RuntimeError:
                pass

    def _serve(self) -> None:
        import asyncio
        from dbus_fast import Variant
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import RequestNameReply
        from dbus_fast.service import ServiceInterface, method

        svc = self

        class Runner(ServiceInterface):
            def __init__(self):
                super().__init__("org.kde.krunner1")

            @method()
            def Actions(self) -> "a(sss)":  # noqa: N802 - D-Bus name
                return []

            @method()
            def Match(self, query: "s") -> "a(sssida{sv})":  # noqa: N802
                return [[mid, text, icon, typ, rel, {k: Variant("s", v) for k, v in props.items()}]
                        for mid, text, icon, typ, rel, props in build_matches(query)]

            @method()
            def Run(self, matchId: "s", actionId: "s"):  # noqa: N802, N803
                try:
                    svc.on_run(matchId)
                except Exception:  # noqa: BLE001 - never break the bus loop
                    pass

        async def main():
            bus = await MessageBus().connect()
            svc._bus = bus
            bus.export(PATH, Runner())
            reply = await bus.request_name(SERVICE)
            if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
                svc.error = f"could not own {SERVICE} ({reply.name}); is another tray running?"
                return
            svc.ok = True
            await bus.wait_for_disconnect()

        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(main())
        except Exception as exc:  # noqa: BLE001
            self.error = f"{exc.__class__.__name__}: {exc}"
        finally:
            self.ok = False
            if self.error:
                import sys
                print(f"mikronous-tray: KRunner integration stopped: {self.error}", file=sys.stderr)
            self._bus = None
            self._loop.close()
