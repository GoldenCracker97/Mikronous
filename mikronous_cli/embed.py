"""`mik embed on|off|status` — the small CPU embedding server behind semantic file search.

A second llama-server (no GPU, ~150 MB model, port 8082) serving ``/v1/embeddings`` with
nomic-embed-text-v1.5. ``on`` downloads the model once, writes ``embed.env``, installs and starts the
service (systemd on Linux, a detached process on Windows), then embeds the document index.
``off`` stops it; the keyword search keeps working either way.
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request

from .paths import CONF_DIR, LLAMA_ENV, read_env
from .platform import IS_WINDOWS

EMBED_ENV = CONF_DIR / "embed.env"
REPO = "nomic-ai/nomic-embed-text-v1.5-GGUF"
FILE_HINT = "Q8_0"
DEFAULTS = {"EMBED_PORT": "8082", "EMBED_CTX": "2048", "EMBED_THREADS": "4", "EMBED_POOLING": "mean",
            "EMBED_PREFIX_DOC": "search_document: ", "EMBED_PREFIX_QUERY": "search_query: "}
UNIT = "mikronous-embed.service"
REPO_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent


def env() -> dict[str, str]:
    e = dict(DEFAULTS)
    e.update(read_env(EMBED_ENV))
    return e


def url() -> str:
    return f"http://127.0.0.1:{env().get('EMBED_PORT', '8082')}"


def answers(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{url()}/v1/models", timeout=timeout):  # noqa: S310
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def enabled() -> bool:
    return EMBED_ENV.exists() and env().get("EMBED_ENABLED", "0") == "1"


def _write_env(values: dict[str, str]) -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Mikronous embedding server (semantic file search). Written by `mik embed on`."]
    lines += [f"{k}={v}" for k, v in values.items()]
    EMBED_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _server() -> dict:
    from mikronous_model import runner
    return runner.server("embed")


def cmd_on() -> int:
    from mikronous_model import apply as apply_mod
    from mikronous_model import runner
    llama = read_env(LLAMA_ENV)
    server = llama.get("LLAMA_SERVER", "")
    if not server:
        print("llama.env has no LLAMA_SERVER; run the installer (or scripts/install-llama.sh) first", file=sys.stderr)
        return 1
    repo, filename, _size = apply_mod.resolve_hf(f"hf:{REPO}", FILE_HINT)
    path = apply_mod.download(repo, filename)
    values = env()
    values.update({"EMBED_ENABLED": "1", "EMBED_SERVER": server, "EMBED_MODEL": str(path)})
    _write_env(values)
    print(f"wrote {EMBED_ENV}")
    if not IS_WINDOWS:
        unit_dir = __import__("pathlib").Path("~/.config/systemd/user").expanduser()
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / UNIT).write_text((REPO_DIR / "systemd" / UNIT).read_text(encoding="utf-8"), encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", UNIT], capture_output=True)
    if not runner.restart("embed"):
        print("could not start the embedding server", file=sys.stderr)
        return 1
    print("waiting for the embedding server ", end="", file=sys.stderr, flush=True)
    for _ in range(60):
        if answers():
            break
        print(".", end="", file=sys.stderr, flush=True)
        time.sleep(1)
    print(file=sys.stderr)
    if not answers():
        print(f"embedding server did not come up on {url()}; see: {runner.restart_hint('embed')}", file=sys.stderr)
        return 1
    print(f"embedding server up at {url()}; embedding the document index (first pass can take a few minutes)")
    from . import docs
    return docs.main(["reindex", "--embed"])


def cmd_off() -> int:
    from mikronous_model import runner
    values = env()
    values["EMBED_ENABLED"] = "0"
    _write_env(values)
    runner.stop("embed")
    if not IS_WINDOWS:
        subprocess.run(["systemctl", "--user", "disable", UNIT], capture_output=True)
    print("semantic search off; keyword search keeps working (mik embed on to re-enable)")
    return 0


def cmd_status() -> int:
    from mikronous_model import runner
    e = env()
    print(f"enabled   {'yes' if enabled() else 'no'}")
    print(f"server    {url()}  {'answers' if answers() else 'not answering'}  ({'running' if runner.is_running('embed') else 'stopped'})")
    print(f"model     {e.get('EMBED_MODEL', '(not downloaded)')}")
    try:
        from . import docs
        s = docs._docs_index().stats()
        print(f"chunks    {s.get('chunks', 0)} embedded across {s.get('files_embedded', 0)} files")
    except Exception as exc:  # noqa: BLE001
        print(f"chunks    ? ({exc.__class__.__name__})")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "on":
        return cmd_on()
    if cmd == "off":
        return cmd_off()
    if cmd == "status":
        return cmd_status()
    print("usage: mik embed [on|off|status]", file=sys.stderr)
    return 2
