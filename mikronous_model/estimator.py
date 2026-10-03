"""Fit a model (weights + KV cache + overhead) into a memory budget.

The estimate is deliberately simple and a little conservative:
  weights   = GGUF file size
  kv cache  = ctx * kv_bytes_per_token(kv quant)
  overhead  = compute buffers + CUDA/Vulkan context: 6% of weights + 384 MiB, plus
              a ctx-proportional scratch (~ ctx * 24 KiB) for prompt batches
"""

from __future__ import annotations

from dataclasses import dataclass

from .gguf import GGUFMeta

GiB = 1024 ** 3
MiB = 1024 ** 2

# bytes per element for llama.cpp KV cache types
KV_BYTES = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32, "q5_0": 22 / 32,
            "q4_1": 20 / 32, "q4_0": 18 / 32}
# tried in order until the model fits; the last that fits wins
KV_LADDER = [("f16", "f16"), ("q8_0", "q8_0"), ("q8_0", "q4_0"), ("q4_0", "q4_0")]
CTX_LADDER = [65536, 49152, 40960, 32768, 24576, 16384, 8192]
HERMES_MIN_CTX = 65536
HEADROOM = 512 * MiB  # left free on the GPU for the desktop / other apps


@dataclass
class Fit:
    ctx: int
    kv_k: str
    kv_v: str
    ngl: int                 # layers on GPU (999 = all)
    weights_bytes: float
    kv_bytes: float
    overhead_bytes: float
    gpu_bytes: float         # what lands on the GPU
    cpu_bytes: float         # weights spilled to system RAM
    budget_bytes: float
    full_offload: bool
    notes: list[str]

    @property
    def total_bytes(self) -> float:
        return self.weights_bytes + self.kv_bytes + self.overhead_bytes

    @property
    def meets_hermes_ctx(self) -> bool:
        return self.ctx >= HERMES_MIN_CTX


def _overhead(weights: float, ctx: int) -> float:
    return weights * 0.06 + 384 * MiB + ctx * 24 * 1024


def estimate(meta: GGUFMeta, ctx: int, kv_k: str, kv_v: str, budget: float, layers_total: int | None = None) -> Fit:
    layers = layers_total or meta.block_count or 32
    weights = float(meta.file_bytes)
    kv = ctx * meta.kv_bytes_per_token(KV_BYTES[kv_k], KV_BYTES[kv_v])
    ovh = _overhead(weights, ctx)
    notes: list[str] = []
    total = weights + kv + ovh
    if total <= budget:
        return Fit(ctx, kv_k, kv_v, 999, weights, kv, ovh, total, 0.0, budget, True, notes)
    # Partial offload: KV + overhead stay on GPU, spill whole layers to RAM.
    per_layer = weights / layers
    room = budget - kv - ovh
    ngl = int(room // per_layer) if room > 0 else 0
    ngl = max(0, min(layers - 1, ngl))
    gpu = ngl * per_layer + kv + ovh if ngl > 0 else 0.0
    cpu = weights - ngl * per_layer
    if ngl == 0:
        notes.append("nothing fits on the GPU at this context; runs on CPU (slow)")
    else:
        notes.append(f"{layers - ngl} of {layers} layers run on the CPU — expect a big slowdown")
    if meta.is_moe:
        notes.append("mixture-of-experts: try LLAMA_EXTRA_ARGS='--n-cpu-moe N' to keep attention on the GPU and experts in RAM")
    return Fit(ctx, kv_k, kv_v, ngl, weights, kv, ovh, gpu, cpu, budget, False, notes)


def best_fit(meta: GGUFMeta, budget: float, target_ctx: int = HERMES_MIN_CTX, prefer_full: bool = True) -> Fit:
    """Pick (ctx, kv quant, ngl): keep ctx >= target by quantising the KV cache first; only
    then shrink the context; only then spill layers to RAM."""
    # 1. target ctx, best KV quant that fully fits
    for kv_k, kv_v in KV_LADDER:
        fit = estimate(meta, target_ctx, kv_k, kv_v, budget)
        if fit.full_offload:
            return fit
    # 2. smaller contexts with the smallest KV quant, fully on GPU
    kv_k, kv_v = KV_LADDER[-1]
    for ctx in CTX_LADDER:
        if ctx >= target_ctx:
            continue
        fit = estimate(meta, ctx, kv_k, kv_v, budget)
        if fit.full_offload:
            fit.notes.append(f"context reduced to {ctx} (< {HERMES_MIN_CTX}); Hermes will compress long sessions")
            if prefer_full:
                return fit
            break
    # 3. keep the target context and spill layers
    fit = estimate(meta, target_ctx, "q8_0", "q4_0", budget)
    return fit


def gpu_budget(vram_total: int, vram_used: int = 0) -> float:
    """Usable VRAM: what's free now minus headroom (never below 0)."""
    return max(0.0, float(vram_total - vram_used) - HEADROOM)


def cpu_budget(ram_available: int) -> float:
    return max(0.0, float(ram_available) - 2 * GiB)
