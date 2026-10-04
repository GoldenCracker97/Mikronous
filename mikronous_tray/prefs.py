"""The preferences behind the tray's Settings dialog, and where each one lives.

tray.json (mikronous_tray.settings)          litany, keep_model, notes_dir, translate_lang, hotkeys (Windows only)
profile SOUL.md (mikronous_cli.voice)        voice: plain | light | full
profile config.yaml (mikronous_cli.privacy)  approvals.mode, internet (web + browser toolsets)
profile .env                                 MIKRONOUS_DOCS_DIRS (folders the file search indexes)

Pure Python (no Qt) so it is testable and reusable from the CLI. ``apply`` writes only what changed
and reports whether the gateway must be restarted for the change to take effect.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from mikronous_cli import privacy, voice
from mikronous_cli.paths import PROFILE, PROFILE_HOME, read_env
from mikronous_cli.platform import IS_WINDOWS, hermes_bin

from . import settings

PROFILE_ENV = PROFILE_HOME / ".env"
VOICES = voice.LEVELS                                   # plain | light | full
APPROVAL_MODES = ("smart", "manual", "off")
GATEWAY_KEYS = ("approvals", "internet", "docs_dirs")   # the gateway reads these at start


@dataclass
class Prefs:
    voice: str = "full"
    approvals: str = "smart"
    internet: bool = True
    litany: bool = True
    keep_model: bool = False
    notes_dir: str = "~/Mikronous/notes"
    docs_dirs: str = "~/Documents"
    hotkey: str = "Ctrl+Alt+Space"
    hotkey_selection: str = "Ctrl+Alt+Shift+Space"
    translate_lang: str = "English"
    hotkey_vox: str = "Ctrl+Alt+V"
    stt_model: str = "base"
    tts: bool = False
    tts_voice: str = "en_GB-alan-medium"
    tts_effect: str = "servitor"
    tts_depth: int = 50
    tts_metal: int = 50
    semantic: bool = False

    def changed_from(self, other: "Prefs") -> list[str]:
        return [f.name for f in fields(self) if getattr(self, f.name) != getattr(other, f.name)]


# ----------------------------------------------------------------------------- read
def read() -> Prefs:
    st = settings.load()
    cfg = privacy.load_config()
    p = Prefs()
    try:
        p.voice = voice.current(voice.SOUL.read_text(encoding="utf-8"))
    except OSError:
        p.voice = "none"
    p.approvals = str(((cfg.get("approvals") or {}).get("mode")) or "smart")
    p.internet = privacy.is_online(cfg) if cfg else True
    p.litany = bool(st.get("litany", True))
    p.keep_model = bool(st.get("keep_model", False))
    p.notes_dir = str(st.get("notes_dir") or Prefs.notes_dir)
    p.docs_dirs = read_env(PROFILE_ENV).get("MIKRONOUS_DOCS_DIRS") or Prefs.docs_dirs
    p.hotkey = str(st.get("hotkey") or Prefs.hotkey)
    p.hotkey_selection = str(st.get("hotkey_selection") or Prefs.hotkey_selection)
    p.translate_lang = str(st.get("translate_lang") or Prefs.translate_lang)
    p.hotkey_vox = str(st.get("hotkey_vox") or Prefs.hotkey_vox)
    p.stt_model = str(st.get("stt_model") or Prefs.stt_model)
    p.tts = bool(st.get("tts", False))
    p.tts_voice = str(st.get("tts_voice") or Prefs.tts_voice)
    p.tts_effect = str(st.get("tts_effect") or Prefs.tts_effect)
    p.tts_depth = _dial(st.get("tts_depth"))
    p.tts_metal = _dial(st.get("tts_metal"))
    try:
        from mikronous_cli import embed
        p.semantic = embed.enabled()
    except Exception:  # noqa: BLE001
        p.semantic = False
    return p


def _dial(v) -> int:
    try:
        return min(max(int(v), 0), 100)
    except (TypeError, ValueError):
        return 50


def model_name() -> str:
    """Basename of the loaded GGUF, from the llama-server env file (no network)."""
    try:
        from mikronous_cli.paths import LLAMA_ENV
        model = read_env(LLAMA_ENV).get("LLAMA_MODEL", "")
        return Path(model).stem if model else ""
    except Exception:  # noqa: BLE001
        return ""


def hotkey_editable() -> bool:
    """On Windows the tray registers the hotkey itself; on KDE it lives in kglobalshortcutsrc."""
    return IS_WINDOWS


def kde_shortcut(action: str = "_launch") -> str:
    try:
        from mikronous_cli.doctor import _shortcut_key
        return _shortcut_key(action)
    except Exception:  # noqa: BLE001
        return ""


# ----------------------------------------------------------------------------- write
def apply(old: Prefs, new: Prefs) -> list[str]:
    """Persist every field that differs; returns the names that changed. Raises ValueError on bad input."""
    changed = new.changed_from(old)
    if not changed:
        return []
    if "voice" in changed:
        if new.voice not in VOICES:
            raise ValueError(f"voice must be one of {', '.join(VOICES)}")
        text = voice.set_level(voice.SOUL.read_text(encoding="utf-8"), new.voice)
        voice.SOUL.write_text(text, encoding="utf-8")
        voice.record_sha(text, voice.SHA_FILE)
    if "approvals" in changed or "internet" in changed:
        if new.approvals not in APPROVAL_MODES:
            raise ValueError(f"approvals must be one of {', '.join(APPROVAL_MODES)}")
        cfg = privacy.load_config()
        if not cfg:
            raise ValueError(f"no profile config at {privacy.CONFIG}; run the installer first")
        if "approvals" in changed:
            cfg["approvals"] = {**(cfg.get("approvals") or {}), "mode": new.approvals}
        if "internet" in changed:
            privacy.apply_online(cfg, new.internet)
        privacy.save_config(cfg)
    if "docs_dirs" in changed:
        _write_env_var(PROFILE_ENV, "MIKRONOUS_DOCS_DIRS", new.docs_dirs.strip())
    if "semantic" in changed:
        start_embed_toggle(new.semantic)
    tray_changes = {k: getattr(new, k) for k in ("litany", "keep_model", "notes_dir", "hotkey", "hotkey_selection", "translate_lang",
                                                 "hotkey_vox", "stt_model", "tts", "tts_voice", "tts_effect", "tts_depth", "tts_metal") if k in changed}
    if tray_changes:
        settings.save(**tray_changes)
    return changed


def needs_gateway_restart(changed: list[str]) -> bool:
    return any(k in GATEWAY_KEYS for k in changed)


def start_embed_toggle(on: bool) -> None:
    """`mik embed on|off` detached (the download + first embedding pass can take minutes); logs to the conf dir."""
    from mikronous_cli.paths import CONF_DIR
    log = open(CONF_DIR / "embed-setup.log", "ab")  # noqa: SIM115 - handed to the child
    kw: dict = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if IS_WINDOWS:
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "mikronous_cli", "embed", "on" if on else "off"], **kw)


def restart_gateway() -> tuple[bool, str]:
    """`hermes -p mikronous gateway restart`; returns (ok, message)."""
    cmd = [hermes_bin(), "-p", PROFILE, "gateway", "restart"]
    kw: dict = {"capture_output": True, "text": True, "timeout": 120}
    if IS_WINDOWS:
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        res = subprocess.run(cmd, **kw)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run {' '.join(cmd)}: {exc}"
    if res.returncode != 0:
        return False, (res.stderr or res.stdout).strip()[-300:] or f"exit {res.returncode}"
    return True, "gateway restarted"


def _write_env_var(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file, replacing an existing (or commented-out) line, keeping everything else."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    line = f"{key}={value}" if value else f"# {key}="
    pat = re.compile(rf"^#?\s*{re.escape(key)}=.*$", re.MULTILINE)
    if pat.search(text):
        text = pat.sub(line, text, count=1)
    else:
        text = text.rstrip("\n") + ("\n" if text.strip() else "") + line + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def as_dict(p: Prefs) -> dict:
    return asdict(p)


if __name__ == "__main__":   # `python -m mikronous_tray.prefs` prints the current values
    for k, v in as_dict(read()).items():
        print(f"{k:<11} {v}")
    sys.exit(0)
