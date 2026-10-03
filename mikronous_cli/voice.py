"""`mik voice [plain|light|full]` — how much Machine Cult the assistant speaks.

Swaps the block between ``<!-- voice:start -->`` and ``<!-- voice:end -->`` in the profile's SOUL.md
with the chosen file from ``profile/voices/`` and updates the sha the installer uses to tell "our
SOUL.md" from a hand-edited one, so a later ``scripts/install.sh`` keeps the choice intact.
New chat sessions pick the voice up; no restart needed.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

from .paths import PROFILE_HOME

LEVELS = ("plain", "light", "full")
REPO_DIR = Path(__file__).resolve().parent.parent
VOICES_DIR = REPO_DIR / "profile" / "voices"
SOUL = PROFILE_HOME / "SOUL.md"
SHA_FILE = PROFILE_HOME / ".mikronous" / "soul.sha"
_BLOCK = re.compile(r"<!-- voice:start -->\n(.*?)<!-- voice:end -->", re.DOTALL)


def current(soul_text: str) -> str:
    m = _BLOCK.search(soul_text)
    if not m:
        return "none"
    head = m.group(1).strip().splitlines()[0] if m.group(1).strip() else ""
    for level in LEVELS:
        if head.lower().startswith(f"## voice: {level}"):
            return level
    return "custom"


def set_level(soul_text: str, level: str) -> str:
    if level not in LEVELS:
        raise ValueError(f"voice must be one of {', '.join(LEVELS)}")
    block = (VOICES_DIR / f"{level}.md").read_text(encoding="utf-8").strip() + "\n"
    if not _BLOCK.search(soul_text):
        raise ValueError("SOUL.md has no <!-- voice:start --> / <!-- voice:end --> block; re-run scripts/install.sh")
    return _BLOCK.sub(lambda _m: f"<!-- voice:start -->\n{block}<!-- voice:end -->", soul_text, count=1)


def record_sha(text: str, sha_file: Path = SHA_FILE) -> None:
    sha_file.parent.mkdir(parents=True, exist_ok=True)
    sha_file.write_text(hashlib.sha256(text.encode("utf-8")).hexdigest() + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if not SOUL.exists():
        print(f"{SOUL} missing; run scripts/install.sh first", file=sys.stderr)
        return 1
    text = SOUL.read_text(encoding="utf-8")
    if not argv or argv[0] in ("status", "show"):
        print(current(text))
        return 0
    level = argv[0].lower()
    if level not in LEVELS:
        print(f"usage: mik voice [{'|'.join(LEVELS)}]", file=sys.stderr)
        return 2
    try:
        new = set_level(text, level)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    SOUL.write_text(new, encoding="utf-8")
    record_sha(new)
    print(f"voice: {level} (new chats use it; press Ctrl+N in the tray)")
    return 0
