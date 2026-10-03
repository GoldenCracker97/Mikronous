"""`mik doctor` — one screen that says which piece of Mikronous is missing or down.

Checks are read-only and never modify anything.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .paths import HERMES_HOME, LLAMA_ENV, PROFILE, PROFILE_HOME, gateway, read_env

OK, WARN, FAIL = "ok", "warn", "FAIL"
_read_env = read_env


def _http_json(url: str, headers: dict[str, str] | None = None, timeout: float = 3.0):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback only
        return json.loads(resp.read().decode("utf-8") or "{}")


def _systemd_active(unit: str) -> str | None:
    if not shutil.which("systemctl"):
        return None
    out = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


def run_checks() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []

    # 1. Hermes itself
    hermes = shutil.which("hermes")
    rows.append(("hermes CLI", OK if hermes else FAIL, hermes or "not on PATH — run the Hermes installer"))

    # 2. Profile
    if PROFILE_HOME.exists():
        soul = PROFILE_HOME / "SOUL.md"
        cfg = PROFILE_HOME / "config.yaml"
        detail = ", ".join(n for n, p in (("SOUL.md", soul), ("config.yaml", cfg)) if p.exists()) or "empty"
        rows.append((f"profile '{PROFILE}'", OK if soul.exists() and cfg.exists() else WARN, f"{PROFILE_HOME} ({detail})"))
    else:
        rows.append((f"profile '{PROFILE}'", FAIL, f"{PROFILE_HOME} missing — run scripts/install.sh"))

    # 3. Plugin symlink
    plugin = PROFILE_HOME / "plugins" / "mikronous"
    if plugin.exists():
        rows.append(("mikronous plugin", OK, f"{plugin} -> {plugin.resolve()}"))
    else:
        rows.append(("mikronous plugin", FAIL, f"{plugin} missing"))

    # 4. llama-server unit + endpoint
    env = _read_env(LLAMA_ENV)
    port = env.get("LLAMA_PORT", "8081")
    model = env.get("LLAMA_MODEL", "")
    if not LLAMA_ENV.exists():
        rows.append(("llama env", FAIL, f"{LLAMA_ENV} missing"))
    else:
        exists = Path(model).expanduser().exists() if model else False
        rows.append(("model file", OK if exists else FAIL, model or "LLAMA_MODEL unset"))
        server = env.get("LLAMA_SERVER", "")
        if server and Path(server).exists():
            prefix = Path(server).parent.parent
            backend = (prefix / "BACKEND").read_text().strip() if (prefix / "BACKEND").exists() else "custom"
            tag = (prefix / "TAG").read_text().strip() if (prefix / "TAG").exists() else ""
            rows.append(("llama-server binary", OK, f"{server} [{backend}{' ' + tag if tag else ''}]"))
        else:
            rows.append(("llama-server binary", FAIL, server or "LLAMA_SERVER unset"))
    state = _systemd_active("mikronous-llama.service")
    rows.append(("mikronous-llama.service", OK if state == "active" else WARN, state or "systemctl unavailable"))
    try:
        data = _http_json(f"http://127.0.0.1:{port}/v1/models")
        ids = [m.get("id") for m in data.get("data", [])]
        rows.append((f"llama-server :{port}", OK, f"models: {', '.join(map(str, ids)) or 'none'}"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        rows.append((f"llama-server :{port}", FAIL, f"unreachable ({exc.__class__.__name__})"))

    # 5. Hermes gateway / API server (multiplexed host gateway or per-profile gateway)
    gw = gateway()
    rows.append(("API_SERVER_KEY", OK if gw.api_key and gw.api_key != "replace-me" else FAIL,
                 "set" if gw.api_key else f"missing in {PROFILE_HOME / '.env'}"))
    if gw.mode == "multiplex":
        denv = _read_env(HERMES_HOME / ".env")
        enabled = denv.get("API_SERVER_ENABLED", "").lower() == "true"
        rows.append(("host API server enabled", OK if enabled else FAIL,
                     "API_SERVER_ENABLED=true in ~/.hermes/.env" if enabled
                     else "add API_SERVER_ENABLED=true (+KEY, PORT) to ~/.hermes/.env — re-run scripts/install.sh"))
    state = _systemd_active(gw.service)
    rows.append((f"{gw.service} ({gw.mode})", OK if state == "active" else WARN, state or "systemctl unavailable"))
    try:
        data = _http_json(f"{gw.v1}/models", headers={"Authorization": f"Bearer {gw.api_key}"})
        ids = [m.get("id") for m in data.get("data", [])]
        rows.append(("Hermes API", OK, f"{gw.v1}  models: {', '.join(map(str, ids)) or 'none'}"))
    except urllib.error.HTTPError as exc:
        rows.append(("Hermes API", FAIL, f"{gw.v1} -> HTTP {exc.code} (wrong key or profile not served)"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        rows.append(("Hermes API", FAIL, f"{gw.v1} unreachable ({exc.__class__.__name__})"))

    # 6. Local-only: nothing in this profile can reach a paid provider
    try:
        from . import privacy
        prows = privacy.assess(privacy.load_config(), _read_env(PROFILE_HOME / ".env"))
        bad = [r[0] for r in prows if r[1] == FAIL]
        rows.append(("local-only", OK if not bad else FAIL, "model, logins, telemetry pinned local" if not bad
                     else f"see `mik privacy status`: {', '.join(bad)}"))
    except Exception as exc:  # noqa: BLE001 - never let the privacy probe break doctor
        rows.append(("local-only", WARN, f"could not evaluate ({exc.__class__.__name__})"))

    # 7. Desktop: tray running? global shortcut registered?
    rows.append(("tray", OK if _tray_running() else WARN,
                 "running (control socket answers)" if _tray_running() else "not running — start with `mik tray` (autostarts at login)"))
    key = _shortcut_key()
    rows.append(("shortcut", OK if key else WARN, f"{key} toggles the chat window" if key
                 else "not registered — run scripts/install.sh (or System Settings > Shortcuts > Mikronous)"))

    return rows


def _tray_running() -> bool:
    """Qt places a QLocalServer named 'mikronous-tray' at $XDG_RUNTIME_DIR/mikronous-tray (or /tmp)."""
    import socket
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    for path in (Path(runtime) / "mikronous-tray", Path("/tmp/mikronous-tray")):
        if not path.exists():
            continue
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(str(path))
                return True
        except OSError:
            continue
    return False


def _shortcut_key() -> str:
    """The key bound to mikronous.desktop in kglobalshortcutsrc (KF5 flat group or KF6 [services][...])."""
    import re
    try:
        text = Path("~/.config/kglobalshortcutsrc").expanduser().read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"^\[(?:services\]\[)?mikronous\.desktop\]\n(?:.*\n)*?_launch=([^,\n]+)", text, re.MULTILINE)
    key = m.group(1).strip() if m else ""
    return "" if key.lower() in ("", "none") else key


def main() -> int:
    from . import __version__
    print(f"Mikronous {__version__}")
    rows = run_checks()
    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        print(f"[{status:>4}] {name:<{width}}  {detail}")
    failed = sum(1 for r in rows if r[1] == FAIL)
    print()
    local_bin = str(Path("~/.local/bin").expanduser())
    if local_bin not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"note: {local_bin} is not on PATH; add it so `mik` and `mikronous` resolve.")
    print("All good." if not failed else f"{failed} check(s) failed. See scripts/install.sh and README.md.")
    return 0 if not failed else 1
