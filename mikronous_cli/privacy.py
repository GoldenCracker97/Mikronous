"""`mik privacy` — show and control what can leave the machine.

status   model endpoint, keys in the profile .env, telemetry, external-login adoption, internet-capable toolsets
offline  disable web + browser toolsets and the keyless web ring, restart the profile
online   re-enable them
"""

from __future__ import annotations

import subprocess
import sys

import yaml

from .paths import HERMES_HOME, PROFILE, PROFILE_HOME, read_env

CONFIG = PROFILE_HOME / "config.yaml"
INTERNET_TOOLSETS = ["web", "browser"]
PAID_TOOLSETS = ["connections", "image_gen", "video_gen", "video", "tts", "x_search", "vision"]
OK, WARN, FAIL = "ok", "warn", "FAIL"


def load_config() -> dict:
    try:
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


def secret_names(env: dict[str, str]) -> list[str]:
    """Env names that look like provider credentials (API_SERVER_KEY is ours and local)."""
    out = []
    for k, v in env.items():
        if not v or k == "API_SERVER_KEY":
            continue
        if k.endswith(("_API_KEY", "_TOKEN", "_SECRET")) or k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
            out.append(k)
    return sorted(out)


def assess(cfg: dict, env: dict[str, str]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    model = cfg.get("model") or {}
    provider = str(model.get("provider", ""))
    base = ((cfg.get("providers") or {}).get("llamacpp") or {}).get("base_url", "")
    local = provider in ("llamacpp", "llama.cpp", "custom", "lmstudio", "ollama") and ("127.0.0.1" in base or "localhost" in base)
    rows.append(("model", OK if local else FAIL, f"{provider} @ {base or 'unset'}" + ("" if local else "  <- not a local endpoint")))

    fb = cfg.get("fallback_providers") or model.get("fallback_providers")
    rows.append(("fallback providers", OK if not fb else WARN, "none" if not fb else f"{fb}"))

    aux = cfg.get("auxiliary") or {}
    overridden = [k for k, v in aux.items() if isinstance(v, dict) and v.get("provider") not in (None, "", "auto")]
    rows.append(("auxiliary tasks", OK if not overridden else WARN, "all on the local model" if not overridden else f"overridden: {overridden}"))

    keys = secret_names(env)
    rows.append(("provider keys in profile .env", OK if not keys else WARN, "none" if not keys else ", ".join(keys)))
    dkeys = secret_names(read_env(HERMES_HOME / ".env"))
    rows.append(("provider keys in default .env", OK if not dkeys else WARN,
                 "none" if not dkeys else f"{', '.join(dkeys)} (default profile only; mikronous never selects them)"))

    adopt = ((cfg.get("auth") or {}).get("adopt_external_logins"))
    rows.append(("borrow Claude Code / Codex logins", OK if adopt is False else FAIL, "off" if adopt is False else "ON (auth.adopt_external_logins)"))

    tm = ((cfg.get("telemetry") or {}).get("shared_metrics") or {})
    rows.append(("telemetry", OK if not tm.get("enabled") and not tm.get("send") else FAIL,
                 f"enabled={tm.get('enabled', False)} send={tm.get('send', False)}"))

    disabled = set((cfg.get("agent") or {}).get("disabled_toolsets") or [])
    missing = [t for t in PAID_TOOLSETS if t not in disabled]
    rows.append(("paid-key toolsets", OK if not missing else WARN, "disabled" if not missing else f"still enabled: {missing}"))

    web = cfg.get("web") or {}
    online = [t for t in INTERNET_TOOLSETS if t not in disabled]
    if online:
        detail = f"{', '.join(online)} on — search via {web.get('search_backend', 'keyless ring')}, keyless free tiers {'on' if web.get('keyless_fallback', True) else 'off'}; no account, no key"
    else:
        detail = "web + browser disabled (offline)"
    rows.append(("internet access", OK, detail))
    return rows


def _restart_profile() -> None:
    res = subprocess.run(["hermes", "-p", PROFILE, "gateway", "restart"], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"(restart the profile yourself: hermes -p {PROFILE} gateway restart)", file=sys.stderr)


def cmd_status() -> int:
    cfg, env = load_config(), read_env(PROFILE_HOME / ".env")
    if not cfg:
        print(f"no config at {CONFIG}; run scripts/install.sh", file=sys.stderr)
        return 1
    rows = assess(cfg, env)
    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        print(f"[{status:>4}] {name:<{width}}  {detail}")
    bad = [r for r in rows if r[1] == FAIL]
    print()
    print("Nothing can enrol you in a paid service." if not bad else f"{len(bad)} problem(s) above.")
    return 0 if not bad else 1


def _set_online(online: bool) -> int:
    cfg = load_config()
    if not cfg:
        print(f"no config at {CONFIG}; run scripts/install.sh", file=sys.stderr)
        return 1
    agent = cfg.setdefault("agent", {}) or {}
    cfg["agent"] = agent
    disabled = list(agent.get("disabled_toolsets") or [])
    if online:
        disabled = [t for t in disabled if t not in INTERNET_TOOLSETS]
    else:
        disabled += [t for t in INTERNET_TOOLSETS if t not in disabled]
    agent["disabled_toolsets"] = disabled
    web = cfg.setdefault("web", {}) or {}
    cfg["web"] = web
    web["keyless_fallback"] = bool(online)
    save_config(cfg)
    print(("online: web + browser enabled, keyless free-tier search on" if online
           else "offline: web + browser disabled, keyless free-tier search off"))
    print("(scripts/install.sh resets this to the repo default; run `mik privacy offline` again after a reinstall)")
    _restart_profile()
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        return cmd_status()
    if cmd == "offline":
        return _set_online(False)
    if cmd == "online":
        return _set_online(True)
    print("usage: mik privacy [status|offline|online]", file=sys.stderr)
    return 2
