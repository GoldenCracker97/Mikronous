"""Settings-dialog preferences: reading and writing each store without touching the real profile."""
import json

import pytest

pytest.importorskip("yaml")
from mikronous_cli import privacy, voice  # noqa: E402
from mikronous_tray import prefs, settings  # noqa: E402


@pytest.fixture
def stores(tmp_path, monkeypatch):
    soul = tmp_path / "SOUL.md"
    soul.write_text("# Soul\n<!-- voice:start -->\n" + (voice.VOICES_DIR / "full.md").read_text().strip()
                    + "\n<!-- voice:end -->\ntail\n", encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  provider: llamacpp\nagent:\n  disabled_toolsets: [tts]\napprovals:\n  mode: smart\nweb:\n  keyless_fallback: true\n")
    env = tmp_path / ".env"
    env.write_text("API_SERVER_KEY=abc\n# MIKRONOUS_DOCS_DIRS=~/Documents\n")
    monkeypatch.setattr(voice, "SOUL", soul)
    monkeypatch.setattr(voice, "SHA_FILE", tmp_path / "soul.sha")
    monkeypatch.setattr(privacy, "CONFIG", cfg)
    monkeypatch.setattr(prefs, "PROFILE_ENV", env)
    monkeypatch.setattr(settings, "STATE_FILE", tmp_path / "tray.json")
    return {"soul": soul, "cfg": cfg, "env": env, "tray": tmp_path / "tray.json"}


def test_read_defaults(stores):
    p = prefs.read()
    assert (p.voice, p.approvals, p.internet, p.litany, p.keep_model) == ("full", "smart", True, True, False)
    assert p.docs_dirs == "~/Documents" and p.notes_dir == "~/Mikronous/notes"


def test_apply_writes_each_store(stores):
    old = prefs.read()
    new = prefs.Prefs(voice="light", approvals="manual", internet=False, litany=False, keep_model=True,
                      notes_dir="~/Notes", docs_dirs="~/Docs, ~/Projects", hotkey="Ctrl+Shift+M",
                      hotkey_selection="Ctrl+Shift+S", translate_lang="German", hotkey_vox="Ctrl+Shift+V",
                      stt_model="small", tts=True, tts_voice="en_GB-alba-medium", semantic=False)
    changed = prefs.apply(old, new)
    assert set(changed) == {"voice", "approvals", "internet", "litany", "keep_model", "notes_dir", "docs_dirs", "hotkey",
                            "hotkey_selection", "translate_lang", "hotkey_vox", "stt_model", "tts", "tts_voice"}
    assert prefs.needs_gateway_restart(changed) and not prefs.needs_gateway_restart(["voice", "litany"])
    assert voice.current(stores["soul"].read_text()) == "light" and stores["soul"].read_text().endswith("tail\n")
    assert (stores["cfg"].parent / "soul.sha").exists()
    cfg = privacy.load_config()
    assert cfg["approvals"]["mode"] == "manual" and not privacy.is_online(cfg) and cfg["web"]["keyless_fallback"] is False
    assert "tts" in cfg["agent"]["disabled_toolsets"]
    env_text = stores["env"].read_text()
    assert "MIKRONOUS_DOCS_DIRS=~/Docs, ~/Projects" in env_text and env_text.startswith("API_SERVER_KEY=abc\n")
    tray = json.loads(stores["tray"].read_text())
    assert tray["litany"] is False and tray["keep_model"] is True and tray["notes_dir"] == "~/Notes" and tray["hotkey"] == "Ctrl+Shift+M"
    again = prefs.read()
    assert again == new
    assert prefs.apply(again, again) == []


def test_apply_rejects_bad_values(stores):
    old = prefs.read()
    with pytest.raises(ValueError):
        prefs.apply(old, prefs.Prefs(voice="loud"))
    with pytest.raises(ValueError):
        prefs.apply(old, prefs.Prefs(approvals="maybe"))


def test_write_env_var_appends_when_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1")
    prefs._write_env_var(env, "MIKRONOUS_DOCS_DIRS", "~/X")
    assert env.read_text() == "A=1\nMIKRONOUS_DOCS_DIRS=~/X\n"
    prefs._write_env_var(env, "MIKRONOUS_DOCS_DIRS", "")
    assert "# MIKRONOUS_DOCS_DIRS=" in env.read_text() and "A=1" in env.read_text()
