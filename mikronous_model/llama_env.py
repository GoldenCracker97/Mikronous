"""Read and write ~/.config/mikronous/llama.env, the file the systemd unit sources."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from mikronous_cli.paths import LLAMA_ENV

from .estimator import Fit
from .gguf import GGUFMeta

DEFAULTS = {
    "LLAMA_SERVER": "", "LLAMA_PORT": "8081", "LLAMA_MODEL": "", "LLAMA_ALIAS": "mikronous-local",
    "LLAMA_CTX": "65536", "LLAMA_NGL": "999", "LLAMA_THREADS": "8", "LLAMA_KV_K": "q8_0", "LLAMA_KV_V": "q4_0",
    "LLAMA_PARALLEL": "1", "LLAMA_EXTRA_ARGS": "", "LLAMA_MMPROJ": "",
}


@dataclass
class LlamaEnv:
    values: dict[str, str]
    path: Path = LLAMA_ENV

    def __getitem__(self, key: str) -> str:
        return self.values.get(key, DEFAULTS.get(key, ""))

    def __setitem__(self, key: str, value) -> None:
        self.values[key] = str(value)

    @classmethod
    def load(cls, path: Path = LLAMA_ENV) -> "LlamaEnv":
        vals = dict(DEFAULTS)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                vals[k.strip()] = v.strip()
        return cls(vals, path)

    def save(self) -> None:
        """Rewrite the file keeping comment lines, updating known keys in place, appending new ones."""
        lines: list[str] = []
        seen: set[str] = set()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^([A-Z_]+)=", line)
                if m and m.group(1) in self.values:
                    key = m.group(1)
                    lines.append(f"{key}={self.values[key]}")
                    seen.add(key)
                else:
                    lines.append(line)
        else:
            lines.append("# Mikronous llama-server settings. Written by `mik model`; edit freely, then:")
            lines.append("#   systemctl --user restart mikronous-llama")
        for key, val in self.values.items():
            if key not in seen:
                lines.append(f"{key}={val}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def apply_fit(self, fit: Fit, meta: GGUFMeta, model_path: Path, threads: int | None = None,
                  mmproj_path: Path | None = None) -> None:
        self["LLAMA_MODEL"] = str(model_path)
        self["LLAMA_MMPROJ"] = str(mmproj_path) if mmproj_path else ""     # a text-only model clears it
        self["LLAMA_CTX"] = fit.ctx
        self["LLAMA_NGL"] = fit.ngl
        self["LLAMA_KV_K"] = fit.kv_k
        self["LLAMA_KV_V"] = fit.kv_v
        if threads:
            self["LLAMA_THREADS"] = threads
        # Drop any previous rope/yarn flags (and their values); re-add them below if needed.
        cleaned: list[str] = []
        skip = 0
        for tok in self["LLAMA_EXTRA_ARGS"].split():
            if skip:
                skip -= 1
                continue
            if tok in ("--rope-scaling", "--rope-scale", "--yarn-orig-ctx"):
                skip = 1
                continue
            cleaned.append(tok)
        extra = cleaned
        if meta.context_length and fit.ctx > meta.context_length:
            factor = math.ceil(fit.ctx / meta.context_length)
            extra += ["--rope-scaling", "yarn", "--rope-scale", str(factor), "--yarn-orig-ctx", str(meta.context_length)]
        self["LLAMA_EXTRA_ARGS"] = " ".join(extra)

    def summary(self) -> str:
        return (f"model   {self['LLAMA_MODEL']}\n"
                f"server  {self['LLAMA_SERVER']}\n"
                f"ctx     {self['LLAMA_CTX']}   gpu layers {self['LLAMA_NGL']}   threads {self['LLAMA_THREADS']}\n"
                f"kv      K={self['LLAMA_KV_K']} V={self['LLAMA_KV_V']}   parallel {self['LLAMA_PARALLEL']}\n"
                f"extra   {self['LLAMA_EXTRA_ARGS'] or '-'}\n"
                f"vision  {self['LLAMA_MMPROJ'] or 'no (text-only model)'}")
