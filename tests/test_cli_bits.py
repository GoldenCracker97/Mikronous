"""Small CLI and installer checks that need no desktop."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_mik_docs_imports_the_plugin_index():
    from mikronous_cli import docs
    mod = docs._docs_index()
    assert callable(mod.search) and callable(mod.reindex) and hasattr(mod, "embed_enabled")


def test_install_sh_shortcut_check_matches_literal_keys():
    snippet = r"""
HOTKEY="Meta+Space"; SEL="Meta+Shift+Space"
SECTION=$'[mikronous.desktop]\n_k_friendly_name=Mikronous\n_launch=Meta+Space,none,Mikronous\nselection=Meta+Shift+Space,none,X\n'
grep -qE "^_launch=${HOTKEY//+/\\+}(,|$)" <<<"$SECTION" && grep -qE "^selection=${SEL//+/\\+}(,|$)" <<<"$SECTION" && echo match
grep -qE "^_launch=$HOTKEY(,|$)" <<<"$SECTION" || echo unescaped-misses
"""
    out = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True).stdout.split()
    assert out == ["match", "unescaped-misses"]
    src = (ROOT / "scripts" / "install.sh").read_text()
    assert "${HOTKEY//+/" in src and "MIKRONOUS_VOICE_INPUT" in src and "TRAY_MARKER" in src
