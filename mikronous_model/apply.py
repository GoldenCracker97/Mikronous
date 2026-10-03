"""Download a GGUF, write llama.env, restart the server, wait for health."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from mikronous_cli.paths import HERMES_HOME

from .gguf import hf_list_gguf, hf_resolve_url

MODELS_DIR = Path(os.environ.get("MIKRONOUS_MODELS_DIR", str(HERMES_HOME / "models"))).expanduser()
UNIT = "mikronous-llama.service"


def resolve_hf(spec: str, hint: str | None = None) -> tuple[str, str, int]:
    """'hf:owner/repo[:file]' -> (repo, filename, size). Without a file, pick by hint (default Q4_K_M)."""
    body = spec[3:] if spec.startswith("hf:") else spec
    repo, _, filename = body.partition(":")
    files = hf_list_gguf(repo)
    if not files:
        raise ValueError(f"no .gguf files in {repo}")
    if filename:
        for name, size in files:
            if name == filename or name.endswith("/" + filename):
                return repo, name, size
        raise ValueError(f"{filename} not in {repo}; available: {', '.join(n for n, _ in files[:12])}")
    hint = (hint or "Q4_K_M").lower()
    # Prefer single-file matches (no -00001-of- shards) containing the hint.
    cands = [(n, s) for n, s in files if hint in n.lower() and "-of-" not in n]
    if not cands:
        cands = [(n, s) for n, s in files if hint in n.lower()]
    if not cands:
        raise ValueError(f"no file matching '{hint}' in {repo}; available: {', '.join(n for n, _ in files[:12])}")
    # Among candidates prefer the plain quant over UD-/XL variants only if hint is a plain quant.
    cands.sort(key=lambda t: (len(t[0]), t[0]))
    return repo, cands[0][0], cands[0][1]


def pick_mmproj(files: list[tuple[str, int]], hint: str = "mmproj") -> tuple[str, int] | None:
    """The vision projector in a repo's file list: prefers F16, then BF16, then the first match."""
    cands = [(n, s) for n, s in files if hint.lower() in n.lower()]
    if not cands:
        return None
    import re
    for pref in (r"(?<!b)f16", r"bf16"):
        for n, s in cands:
            if re.search(pref, n.lower()):
                return n, s
    return cands[0]


def resolve_mmproj(repo: str, hint: str = "mmproj") -> tuple[str, int]:
    picked = pick_mmproj(hf_list_gguf(repo), hint)
    if picked is None:
        raise ValueError(f"no {hint} file in {repo}")
    return picked


def download(repo: str, filename: str, dest_dir: Path = MODELS_DIR) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(filename).name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    url = hf_resolve_url(repo, filename)
    print(f"downloading {repo}/{filename} -> {dest}", file=sys.stderr)
    hf = shutil.which("hf") or shutil.which("huggingface-cli")
    if hf:
        subprocess.run([hf, "download", repo, filename, "--local-dir", str(dest_dir)], check=True)
        got = dest_dir / filename
        if got != dest and got.exists():
            got.replace(dest)
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    subprocess.run(["curl", "-L", "--fail", "--retry", "3", "-C", "-", "-o", str(part), url], check=True)
    part.replace(dest)
    return dest


def restart_and_wait(port: str = "8081", timeout: float = 180.0) -> bool:
    from . import runner
    if not runner.available():
        print("no service manager available; restart llama-server yourself", file=sys.stderr)
        return False
    if not runner.restart():
        print("could not restart llama-server", file=sys.stderr)
        return False
    print(f"restarting llama-server ({runner.mode()}), waiting for :{port} ", end="", file=sys.stderr, flush=True)
    if runner.wait_until_up(timeout, progress=lambda: print(".", end="", file=sys.stderr, flush=True)):
        print(" up", file=sys.stderr)
        return True
    print("\nllama-server did not come up. Last log lines:", file=sys.stderr)
    print(runner.recent_log(25), file=sys.stderr)
    return False
