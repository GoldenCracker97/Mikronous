"""Where Mikronous's Hermes pieces live, and how to reach the API server.

Shared by `mik doctor`, `mik model` and the tray app so the gateway topology is decided in
exactly one place:

* **multiplexed** (Hermes >= 0.21): one host gateway (`hermes-gateway.service`, the default
  profile) serves every profile. The mikronous API server is mirrored on the default
  profile's listener under ``/p/mikronous`` and authenticated with mikronous's own
  ``API_SERVER_KEY``.
* **standalone** (older Hermes, or ``gateway.standalone: true`` in the profile config): the
  profile runs its own ``hermes-gateway-mikronous.service`` and its own listener.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .platform import IS_WINDOWS, conf_dir, hermes_home

PROFILE = "mikronous"
HERMES_HOME = hermes_home()
PROFILE_HOME = HERMES_HOME / "profiles" / PROFILE
CONF_DIR = conf_dir()
LLAMA_ENV = CONF_DIR / "llama.env"
USER_UNIT_DIR = Path("~/.config/systemd/user").expanduser()


def read_env(path: Path) -> dict[str, str]:
    """Minimal KEY=value parser for Hermes .env files (comments and quotes tolerated)."""
    env: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.split(" #", 1)[0].strip().strip('"').strip("'")
    return env


def _yaml_flag(path: Path, key: str) -> bool | None:
    """True/False when ``key: true|false`` appears anywhere in the file (any indentation)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(rf"^\s*{re.escape(key)}:\s*(true|false)\b", text, re.MULTILINE | re.IGNORECASE)
    return None if m is None else m.group(1).lower() == "true"


@dataclass(frozen=True)
class Gateway:
    mode: str            # "multiplex" | "standalone"
    service: str         # systemd user unit name
    base_url: str        # API root without /v1, e.g. http://127.0.0.1:8642/p/mikronous
    api_key: str         # mikronous's own API_SERVER_KEY

    @property
    def v1(self) -> str:
        return f"{self.base_url}/v1"


def gateway() -> Gateway:
    profile_env = read_env(PROFILE_HOME / ".env")
    default_env = read_env(HERMES_HOME / ".env")
    key = profile_env.get("API_SERVER_KEY", "")

    standalone = _yaml_flag(PROFILE_HOME / "config.yaml", "standalone") is True
    multiplexed = (
        not standalone
        and (
            _yaml_flag(HERMES_HOME / "config.yaml", "multiplex_profiles") is True
            or (USER_UNIT_DIR / "hermes-gateway.service").exists()
        )
    )
    if multiplexed:
        host = default_env.get("API_SERVER_HOST", "127.0.0.1")
        port = default_env.get("API_SERVER_PORT", "8642")
        return Gateway("multiplex", "hermes-gateway.service", f"http://{host}:{port}/p/{PROFILE}", key)
    host = profile_env.get("API_SERVER_HOST", "127.0.0.1")
    port = profile_env.get("API_SERVER_PORT", "8642")
    service = f"Hermes_Gateway_{PROFILE} (scheduled task)" if IS_WINDOWS else f"hermes-gateway-{PROFILE}.service"
    return Gateway("standalone", service, f"http://{host}:{port}", key)
