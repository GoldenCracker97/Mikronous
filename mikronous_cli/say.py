"""`mik say "text" [--effect servitor|vox-caster|cogitator|none] [--voice <piper voice>] [--out file.wav]`

Speak a line with the local Piper voice through the machine-spirit effect chain (the same path the tray uses
for read-aloud). First use downloads the voice once; nothing leaves the machine after that.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str]) -> int:
    try:
        from mikronous_tray import voice_fx, voice_io
    except ImportError as exc:
        print(f"voice output needs the voice extra: scripts/install.sh --voice ({exc})", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(prog="mik say", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="+")
    ap.add_argument("--effect", default=None, choices=list(voice_fx.ORDER))
    ap.add_argument("--voice", default=None)
    ap.add_argument("--out", default=None, help="write the WAV here instead of playing it")
    a = ap.parse_args(argv)
    ok, msg = voice_io.tts_available()
    if not ok:
        print(msg, file=sys.stderr)
        return 2
    from mikronous_tray import settings
    st = settings.load()
    voice = a.voice or st.get("tts_voice") or voice_io.DEFAULT_TTS_VOICE
    effect = a.effect or st.get("tts_effect") or voice_io.DEFAULT_TTS_EFFECT
    text = " ".join(a.text)
    if a.out:
        voice_io.render(text, a.out, voice, effect)
        print(a.out)
        return 0
    voice_io.Speaker().say(text, voice, effect)
    return 0
