"""``python -m mikronous_tray [toggle|show|hide|new|quit]``: run the tray, or control a running one.

With no argument: start the tray (if one is already running, just show its window).
``toggle`` is what the Meta+Space shortcut (packaging/mikronous.desktop) runs: it starts the tray
when needed, otherwise toggles the chat window.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    if cmd in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    try:
        from .app import send_control
    except ImportError as exc:
        print(f"mikronous-tray: PySide6 is not installed in this environment ({exc}).\n"
              f"Install with: pip install 'mikronous[tray]'  (or re-run scripts/install.sh)", file=sys.stderr)
        return 2

    if cmd:
        if send_control(cmd):
            return 0
        if cmd in ("hide", "quit"):
            return 0                                   # nothing running: nothing to do
        if os.environ.get("MIKRONOUS_TRAY_CHILD"):
            return 1
        # Not running: start a detached tray and let it show the window.
        env = {**os.environ, "MIKRONOUS_TRAY_CHILD": "1"}
        subprocess.Popen([sys.executable, "-m", "mikronous_tray"], env=env, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):                            # up to ~8 s for Qt to come up
            time.sleep(0.2)
            if send_control("show" if cmd in ("toggle", "show") else cmd):
                return 0
        print("mikronous-tray: started, but it did not answer; run `python -m mikronous_tray` in a terminal to see why",
              file=sys.stderr)
        return 1

    if send_control("show"):
        return 0                                       # single instance
    return _run_tray()


def _run_tray() -> int:
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    from .app import TrayApp
    app = QApplication(sys.argv[:1])
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("mikronous-tray: no system tray available; the chat window will still open", file=sys.stderr)
    tray = TrayApp(app)
    if "--show" in sys.argv or not QSystemTrayIcon.isSystemTrayAvailable():
        tray.window.show_window()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
