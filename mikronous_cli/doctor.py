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

PROFILE = "mikronous"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
PROFILE_HOME = HERMES_HOME / "profiles" / PROFILE
LLAMA_ENV = Path("~/.config/mikronous/llama.env").expanduser()
OK, WARN, FAIL = "ok", "warn", "FAIL"


def _read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.split(" #", 1)[0].strip().strip('"').strip("'")
    return env


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

    # 5. Hermes gateway / API server
    penv = _read_env(PROFILE_HOME / ".env")
    api_port = penv.get("API_SERVER_PORT", "8642")
    key = penv.get("API_SERVER_KEY", "")
    rows.append(("API_SERVER_KEY", OK if key and key != "replace-me" else FAIL, "set" if key else "missing in profile .env"))
    state = _systemd_active(f"hermes-gateway-{PROFILE}.service")
    rows.append((f"hermes-gateway-{PROFILE}.service", OK if state == "active" else WARN, state or "systemctl unavailable"))
    try:
        _http_json(f"http://127.0.0.1:{api_port}/health", headers={"Authorization": f"Bearer {key}"})
        rows.append((f"Hermes API server :{api_port}", OK, "healthy"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        rows.append((f"Hermes API server :{api_port}", FAIL, f"unreachable ({exc.__class__.__name__})"))

    return rows


def main() -> int:
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
