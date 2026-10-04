"""Curated small-model presets plus Hermes's own local-model catalog.

A preset carries enough architecture facts to estimate memory BEFORE downloading. The exact
GGUF filename is resolved on the user's machine from the Hugging Face file list (``file_hint``
is a substring such as ``Q4_K_M``), so a repo renaming its files does not break us.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .gguf import GGUFMeta

GiB = 1024 ** 3
VISION_HEADROOM = int(1.0 * GiB)    # compute buffers of the image encoder, on top of the projector weights


@dataclass
class Preset:
    id: str
    label: str
    repo: str
    file_hint: str
    approx_bytes: int
    block_count: int
    head_count: int
    head_count_kv: int
    head_dim: int
    context_length: int
    expert_count: int = 0
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = "mikronous"
    mmproj: str = ""              # substring of the vision projector file in the same repo ("mmproj"); "" = text-only
    mmproj_bytes: int = 0         # its size, counted into the memory budget

    @property
    def vision(self) -> bool:
        return bool(self.mmproj)

    def meta(self, file_bytes: int | None = None) -> GGUFMeta:
        return GGUFMeta(
            path=f"hf:{self.repo}", file_bytes=(file_bytes or self.approx_bytes) + self.mmproj_bytes + (VISION_HEADROOM if self.vision else 0),
            arch=self.id, name=self.label,
            block_count=self.block_count, head_count=self.head_count, head_count_kv=self.head_count_kv,
            embedding_length=self.head_count * self.head_dim, key_length=self.head_dim, value_length=self.head_dim,
            context_length=self.context_length, expert_count=self.expert_count,
        )


# Ordered roughly by capability (and size). All are instruction-tuned with native tool calling.
PRESETS: list[Preset] = [
    Preset("qwen3-1.7b", "Qwen3 1.7B (Q4_K_M)", "unsloth/Qwen3-1.7B-GGUF", "Q4_K_M", int(1.2 * GiB),
           28, 16, 8, 128, 40960, notes="tiny; CPU-only machines and 4 GB GPUs", tags=["cpu", "4gb"]),
    Preset("qwen3-4b-instruct-2507", "Qwen3 4B Instruct 2507 (Q4_K_M)", "unsloth/Qwen3-4B-Instruct-2507-GGUF", "Q4_K_M",
           int(2.4 * GiB), 36, 32, 8, 128, 262144, notes="default for 8 GB GPUs; 256k native context", tags=["8gb"]),
    Preset("qwen3-8b", "Qwen3 8B (Q4_K_M)", "unsloth/Qwen3-8B-GGUF", "Q4_K_M", int(4.9 * GiB),
           36, 32, 8, 128, 40960, notes="stronger reasoning; 32k native context, YaRN to 64k+", tags=["8gb", "12gb"]),
    Preset("qwen3-vl-4b-instruct", "Qwen3-VL 4B Instruct (Q4_K_M) + vision", "unsloth/Qwen3-VL-4B-Instruct-GGUF", "Q4_K_M",
           int(2.5 * GiB), 36, 32, 8, 128, 262144, notes="sees images (screen questions); 8 GB GPUs", tags=["8gb", "vision"],
           mmproj="mmproj", mmproj_bytes=int(0.8 * GiB)),
    Preset("qwen3-vl-8b-instruct", "Qwen3-VL 8B Instruct (Q4_K_M) + vision", "unsloth/Qwen3-VL-8B-Instruct-GGUF", "Q4_K_M",
           int(5.0 * GiB), 36, 32, 8, 128, 262144, notes="sees images; 12 GB GPUs", tags=["12gb", "vision"],
           mmproj="mmproj", mmproj_bytes=int(1.1 * GiB)),
    Preset("qwen3-14b", "Qwen3 14B (Q4_K_M)", "unsloth/Qwen3-14B-GGUF", "Q4_K_M", int(8.6 * GiB),
           40, 40, 8, 128, 40960, notes="12-16 GB GPUs", tags=["12gb", "16gb"]),
    Preset("gpt-oss-20b", "gpt-oss 20B (MXFP4)", "unsloth/gpt-oss-20b-GGUF", "F16", int(12.9 * GiB),
           24, 64, 8, 64, 131072, expert_count=32, notes="OpenAI open-weight MoE; 16 GB GPUs", tags=["16gb"]),
    Preset("qwen3-30b-a3b-instruct-2507", "Qwen3 30B-A3B Instruct 2507 (Q4_K_M)",
           "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF", "Q4_K_M", int(17.3 * GiB), 48, 32, 4, 128, 262144,
           expert_count=128, notes="fast MoE; 24 GB GPUs, or 16 GB with --n-cpu-moe", tags=["24gb", "16gb"]),
    Preset("qwen3-32b", "Qwen3 32B (Q4_K_M)", "unsloth/Qwen3-32B-GGUF", "Q4_K_M", int(18.5 * GiB),
           64, 64, 8, 128, 40960, notes="dense; 24 GB GPUs", tags=["24gb"]),
]


def hermes_catalog_path() -> Path | None:
    """catalog.json from the installed Hermes checkout, if present."""
    for base in (os.environ.get("HERMES_AGENT_DIR"), "~/.hermes/hermes-agent"):
        if not base:
            continue
        p = Path(base).expanduser() / "hermes_cli" / "local_runtime" / "catalog.json"
        if p.exists():
            return p
    return None


def hermes_catalog() -> list[Preset]:
    """Hermes's curated (large) models, converted to presets. Best effort: unknown fields are skipped."""
    path = hermes_catalog_path()
    if not path:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    models = data.get("models", data) if isinstance(data, dict) else data
    out: list[Preset] = []
    for m in models:
        try:
            variants = m.get("variants") or []
            if not variants:
                continue
            v = variants[0]
            files = v.get("files") or []
            size = sum(int(f.get("size_bytes") or f.get("bytes") or 0) for f in files)
            first = files[0].get("path", "") if files else ""
            layers = int(m.get("full_layers") or m.get("layers") or 0)
            kvh = int(m.get("n_head_kv") or m.get("head_count_kv") or 8)
            heads = int(m.get("n_head") or m.get("head_count") or kvh)
            hdim = int(m.get("head_dim") or 128)
            out.append(Preset(
                id=f"hermes:{m['id']}", label=f"{m.get('display_name', m['id'])} ({v.get('quant', '')})",
                repo=m.get("repo", ""), file_hint=first or v.get("quant", ""), approx_bytes=size or 16 * GiB,
                block_count=layers or 48, head_count=heads, head_count_kv=kvh, head_dim=hdim,
                context_length=int(m.get("n_ctx_train") or 131072), expert_count=1 if not m.get("moe") else 64,
                notes=m.get("description", ""), tags=["hermes-catalog"], source="hermes",
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def all_presets() -> list[Preset]:
    return PRESETS + hermes_catalog()


def find_preset(name: str) -> Preset | None:
    name = name.lower()
    for p in all_presets():
        if p.id.lower() == name or p.repo.lower() == name:
            return p
    return None
