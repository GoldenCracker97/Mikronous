"""Quick speed check against the running llama-server (uses its `timings` field)."""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request

_PROMPT_PARA = ("Mikronous is a small local assistant for a KDE desktop. It keeps notes, sets reminders, "
                "searches the user's documents and answers briefly. ")


def run(port: str = "8081", prompt_tokens_target: int = 1500, gen_tokens: int = 96) -> dict:
    prompt = _PROMPT_PARA * max(1, prompt_tokens_target // 30)
    body = json.dumps({
        "model": "mikronous-local", "max_tokens": gen_tokens, "temperature": 0,
        "messages": [{"role": "user", "content": prompt + "\n\nSummarise the above in two sentences."}],
    }).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    wall = time.time() - t0
    timings = data.get("timings") or {}
    usage = data.get("usage") or {}
    out = {
        "wall_s": round(wall, 2),
        "prompt_tokens": usage.get("prompt_tokens") or timings.get("prompt_n"),
        "completion_tokens": usage.get("completion_tokens") or timings.get("predicted_n"),
        "prompt_tps": round(timings.get("prompt_per_second") or 0, 1),
        "gen_tps": round(timings.get("predicted_per_second") or 0, 1),
    }
    try:
        smi = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        if smi:
            used, total = [int(float(x)) for x in re.split(r",\s*", smi[0])]
            out["vram_used_mib"], out["vram_total_mib"] = used, total
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return out
