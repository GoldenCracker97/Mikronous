"""Desktop helpers behind the desktop tools: notifications, opening things, clipboard.

KDE / freedesktop on Linux; Windows toasts, ``os.startfile`` and PowerShell's clipboard on Windows.

Every function returns a JSON-serialisable dict and never raises on a missing binary — the
gateway may run under systemd without a full desktop environment, and the model needs a
readable error, not a traceback.
"""

from __future__ import annotations

import configparser
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

IS_WINDOWS = sys.platform == "win32"

URGENCY = {"low": 0, "normal": 1, "critical": 2}
APP_DIRS = [Path("~/.local/share/applications").expanduser(), Path("/usr/share/applications"),
            Path("/var/lib/flatpak/exports/share/applications"),
            Path("~/.local/share/flatpak/exports/share/applications").expanduser(),
            Path("/var/lib/snapd/desktop/applications")]


def _env() -> dict[str, str]:
    """Process env with a session bus address even when started by systemd without one."""
    env = dict(os.environ)
    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
    env.setdefault("DISPLAY", ":0")
    return env


def _run(cmd: list[str], timeout: float = 10.0, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_env(), input=input_text)


def _gvariant_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


# ----------------------------------------------------------------------------- notifications
def notify(title: str, body: str = "", urgency: str = "normal", timeout_ms: int | None = None,
           icon: str = "dialog-information", app_name: str = "Mikronous") -> dict:
    title = (title or "Mikronous").strip()[:200]
    body = (body or "").strip()[:2000]
    level = URGENCY.get(str(urgency).lower(), 1)
    if IS_WINDOWS:
        return _notify_windows(title, body)
    if timeout_ms is None:
        # 0 = stays until dismissed (critical); otherwise 10 s. Never pass -1: gdbus reads it as a flag.
        timeout_ms = 0 if level == 2 else 10000
    timeout_ms = max(0, int(timeout_ms))
    if shutil.which("gdbus"):
        try:
            res = _run(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                        "--object-path", "/org/freedesktop/Notifications",
                        "--method", "org.freedesktop.Notifications.Notify",
                        app_name, "0", icon, title, body, "[]", f"{{'urgency': <byte {level}>}}", str(timeout_ms)])
            if res.returncode == 0:
                digits = "".join(ch for ch in res.stdout if ch.isdigit())
                return {"ok": True, "via": "dbus", "id": int(digits) if digits else None}
            err = res.stderr.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            err = str(exc)
    else:
        err = "gdbus not installed"
    if shutil.which("notify-send"):
        try:
            res = _run(["notify-send", "-a", app_name, "-u", str(urgency).lower() if urgency in URGENCY else "normal",
                        "-i", icon, title, body])
            if res.returncode == 0:
                return {"ok": True, "via": "notify-send"}
            err = res.stderr.strip() or err
        except (OSError, subprocess.SubprocessError) as exc:
            err = str(exc)
    return {"error": f"could not show a notification: {err}"}


# ----------------------------------------------------------------------------- open things
def _looks_like_url(target: str) -> bool:
    p = urlparse(target)
    return bool(p.scheme and (p.netloc or p.scheme in ("mailto", "tel")))


def _desktop_files() -> list[Path]:
    out: list[Path] = []
    for d in APP_DIRS:
        if d.is_dir():
            out.extend(sorted(d.glob("*.desktop")))
    return out


def _desktop_entry(path: Path) -> dict:
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
        sec = cp["Desktop Entry"]
    except (configparser.Error, KeyError, OSError, UnicodeDecodeError):
        return {}
    if sec.get("NoDisplay", "false").lower() == "true" or sec.get("Hidden", "false").lower() == "true":
        return {}
    return {"path": path, "name": sec.get("Name", path.stem), "exec": sec.get("Exec", ""),
            "generic": sec.get("GenericName", ""), "keywords": sec.get("Keywords", "")}


def find_app(query: str) -> list[dict]:
    q = query.strip().lower()
    scored: list[tuple[int, dict]] = []
    for f in _desktop_files():
        e = _desktop_entry(f)
        if not e:
            continue
        name = e["name"].lower()
        stem = f.stem.lower()
        exe = e["exec"].split()[0].rsplit("/", 1)[-1].lower() if e["exec"] else ""
        score = 0
        if q in (name, stem, exe):
            score = 100
        elif name.startswith(q) or stem.endswith("." + q) or exe == q:
            score = 80
        elif q in name or q in stem:
            score = 60
        elif q in e["generic"].lower() or q in e["keywords"].lower():
            score = 40
        if score:
            scored.append((score, e))
    scored.sort(key=lambda t: (-t[0], t[1]["name"]))
    return [e for _, e in scored[:8]]


def open_target(target: str, kind: str = "auto") -> dict:
    target = (target or "").strip()
    if not target:
        return {"error": "nothing to open"}
    if kind == "auto":
        kind = "url" if _looks_like_url(target) else "file" if Path(target).expanduser().exists() else "app"
    if IS_WINDOWS:
        return _open_windows(target, kind)
    if kind in ("url", "file"):
        path_or_url = str(Path(target).expanduser()) if kind == "file" else target
        if kind == "file" and not Path(path_or_url).exists():
            return {"error": f"file not found: {path_or_url}"}
        for opener in (["xdg-open"], ["kioclient", "exec"], ["gio", "open"]):
            if shutil.which(opener[0]):
                try:
                    subprocess.Popen(opener + [path_or_url], env=_env(), stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
                    return {"ok": True, "opened": path_or_url, "via": opener[0]}
                except OSError as exc:
                    return {"error": str(exc)}
        return {"error": "no opener found (xdg-open / kioclient / gio)"}
    # application by name
    matches = find_app(target)
    if not matches:
        return {"error": f"no installed application matches '{target}'"}
    app = matches[0]
    launchers = (["gio", "launch", str(app["path"])], ["kioclient", "exec", str(app["path"])],
                 ["gtk-launch", app["path"].stem])
    for cmd in launchers:
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd, env=_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return {"ok": True, "launched": app["name"], "desktop_file": str(app["path"]),
                        "other_matches": [m["name"] for m in matches[1:4]]}
            except OSError as exc:
                return {"error": str(exc)}
    # last resort: run the Exec line without field codes, no shell
    exec_line = " ".join(tok for tok in app["exec"].split() if not tok.startswith("%"))
    try:
        subprocess.Popen(shlex.split(exec_line), env=_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return {"ok": True, "launched": app["name"], "via": "exec"}
    except (OSError, ValueError) as exc:
        return {"error": f"could not launch {app['name']}: {exc}"}


# ----------------------------------------------------------------------------- clipboard
def clipboard_get() -> dict:
    if IS_WINDOWS:
        return _clipboard_windows_get()
    attempts = (
        ["qdbus6", "org.kde.klipper", "/klipper", "org.kde.klipper.klipper.getClipboardContents"],
        ["qdbus", "org.kde.klipper", "/klipper", "org.kde.klipper.klipper.getClipboardContents"],
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    )
    errs = []
    for cmd in attempts:
        if not shutil.which(cmd[0]):
            continue
        try:
            res = _run(cmd, timeout=5)
            if res.returncode == 0:
                text = res.stdout
                return {"ok": True, "text": text[:20000], "truncated": len(text) > 20000, "via": cmd[0]}
            errs.append(f"{cmd[0]}: {res.stderr.strip()}")
        except (OSError, subprocess.SubprocessError) as exc:
            errs.append(f"{cmd[0]}: {exc}")
    return {"error": "clipboard unavailable: " + ("; ".join(errs) or "no clipboard tool found (qdbus6 / wl-paste / xclip)")}


def clipboard_set(text: str) -> dict:
    text = text if text is not None else ""
    if IS_WINDOWS:
        return _clipboard_windows_set(text)
    attempts = (
        (["qdbus6", "org.kde.klipper", "/klipper", "org.kde.klipper.klipper.setClipboardContents", text], None),
        (["qdbus", "org.kde.klipper", "/klipper", "org.kde.klipper.klipper.setClipboardContents", text], None),
        (["wl-copy"], text),
        (["xclip", "-selection", "clipboard"], text),
        (["xsel", "--clipboard", "--input"], text),
    )
    errs = []
    for cmd, stdin in attempts:
        if not shutil.which(cmd[0]):
            continue
        try:
            res = _run(cmd, timeout=5, input_text=stdin)
            if res.returncode == 0:
                return {"ok": True, "chars": len(text), "via": cmd[0]}
            errs.append(f"{cmd[0]}: {res.stderr.strip()}")
        except (OSError, subprocess.SubprocessError) as exc:
            errs.append(f"{cmd[0]}: {exc}")
    return {"error": "clipboard unavailable: " + ("; ".join(errs) or "no clipboard tool found")}


# ----------------------------------------------------------------------------- Windows
_PS = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]
# PowerShell's own AppUserModelID: toasts shown under it need no registration of our own.
_PS_AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_TOAST_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName("text")
$n.Item(0).AppendChild($t.CreateTextNode($env:MIK_TITLE)) | Out-Null
$n.Item(1).AppendChild($t.CreateTextNode($env:MIK_BODY)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:MIK_AUMID).Show($toast)
"""


def _notify_windows(title: str, body: str) -> dict:
    env = {**os.environ, "MIK_TITLE": title, "MIK_BODY": body, "MIK_AUMID": _PS_AUMID}
    try:
        res = subprocess.run(_PS + [_TOAST_PS], env=env, capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"toast failed: {exc}"}
    if res.returncode == 0:
        return {"ok": True, "via": "windows-toast"}
    return {"error": f"toast failed: {res.stderr.strip()[:300]}"}


_START_MENU_DIRS = [Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs"]


def _find_shortcut(name: str) -> list[Path]:
    needle = name.lower()
    hits: list[tuple[int, Path]] = []
    for base in _START_MENU_DIRS:
        if not base.exists():
            continue
        for lnk in base.rglob("*.lnk"):
            stem = lnk.stem.lower()
            if stem == needle:
                hits.append((0, lnk))
            elif stem.startswith(needle):
                hits.append((1, lnk))
            elif needle in stem:
                hits.append((2, lnk))
    hits.sort(key=lambda t: (t[0], len(t[1].stem)))
    return [p for _, p in hits]


def _open_windows(target: str, kind: str) -> dict:
    if kind in ("url", "file"):
        path_or_url = str(Path(target).expanduser()) if kind == "file" else target
        if kind == "file" and not Path(path_or_url).exists():
            return {"error": f"file not found: {path_or_url}"}
        try:
            os.startfile(path_or_url)  # noqa: S606
            return {"ok": True, "opened": path_or_url, "via": "startfile"}
        except OSError as exc:
            return {"error": str(exc)}
    exe = shutil.which(target)
    if exe:
        try:
            subprocess.Popen([exe], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            return {"ok": True, "launched": target, "via": "path"}
        except OSError as exc:
            return {"error": str(exc)}
    matches = _find_shortcut(target)
    if not matches:
        return {"error": f"no installed application matches '{target}'"}
    try:
        os.startfile(str(matches[0]))  # noqa: S606
        return {"ok": True, "launched": matches[0].stem, "shortcut": str(matches[0]),
                "other_matches": [m.stem for m in matches[1:4]]}
    except OSError as exc:
        return {"error": str(exc)}


def _clipboard_windows_get() -> dict:
    try:
        res = subprocess.run(_PS + ["Get-Clipboard -Raw"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"clipboard unavailable: {exc}"}
    if res.returncode != 0:
        return {"error": f"clipboard unavailable: {res.stderr.strip()[:200]}"}
    text = res.stdout
    return {"ok": True, "text": text[:20000], "truncated": len(text) > 20000, "via": "powershell"}


def _clipboard_windows_set(text: str) -> dict:
    try:
        res = subprocess.run(_PS + ["Set-Clipboard -Value ([Console]::In.ReadToEnd())"], input=text,
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"clipboard unavailable: {exc}"}
    if res.returncode != 0:
        return {"error": f"clipboard unavailable: {res.stderr.strip()[:200]}"}
    return {"ok": True, "chars": len(text), "via": "powershell"}
