"""`mik model` command line."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from mikronous_cli.paths import LLAMA_ENV

from . import apply as apply_mod
from . import bench as bench_mod
from .estimator import HERMES_MIN_CTX, KV_BYTES, Fit, best_fit, cpu_budget, estimate, gpu_budget
from .gguf import GGUFMeta, hf_resolve_url, read_meta
from .hardware import HardwareProfile, detect, fmt_bytes
from .llama_env import LlamaEnv
from .presets import Preset, all_presets, find_preset

REPO_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- helpers
def _budget(hw: HardwareProfile, backend_cpu: bool = False) -> tuple[float, str]:
    gpu = hw.gpu
    if gpu and gpu.vram_bytes and not backend_cpu:
        # Our own server may be holding VRAM right now; count it as free.
        used = gpu.vram_used_bytes
        if _llama_running():
            used = 0
        return gpu_budget(gpu.vram_bytes, used), f"GPU {gpu.name} ({fmt_bytes(gpu.vram_bytes)} VRAM)"
    return cpu_budget(hw.ram_available_bytes), f"CPU / system RAM ({fmt_bytes(hw.ram_bytes)})"


def _llama_running() -> bool:
    try:
        from . import runner
        return runner.is_running()
    except Exception:  # noqa: BLE001
        return False


def _has_gpu(hw: HardwareProfile) -> bool:
    return hw.gpu is not None and hw.gpu.vram_bytes > 0


def _print_hw(hw: HardwareProfile) -> None:
    if hw.gpus:
        for g in hw.gpus:
            extra = f", driver {g.driver}" if g.driver else ""
            extra += f", CUDA {g.cuda_version}" if g.cuda_version else ""
            print(f"GPU   {g.name} [{g.vendor}]  {fmt_bytes(g.vram_bytes)} VRAM ({fmt_bytes(g.vram_used_bytes)} in use{extra})")
    else:
        print("GPU   none detected (CPU inference)")
    print(f"RAM   {fmt_bytes(hw.ram_bytes)} total, {fmt_bytes(hw.ram_available_bytes)} available")
    print(f"CPU   {hw.cpu_threads} threads  {hw.cpu_name}")


def _print_fit(label: str, fit: Fit, meta: GGUFMeta, gpu: bool = True) -> None:
    if not gpu:
        where = "fits in system RAM (CPU inference)" if fit.full_offload else "does not fit in system RAM"
    else:
        where = "fits on the GPU" if fit.full_offload else (f"{fit.ngl} layers on GPU, rest in RAM" if fit.ngl else "CPU only")
    ctx_note = "" if fit.meets_hermes_ctx else f"  (below Hermes's {HERMES_MIN_CTX} target)"
    print(f"{label}")
    print(f"  weights {fmt_bytes(fit.weights_bytes)}  + KV cache {fmt_bytes(fit.kv_bytes)} ({fit.kv_k}/{fit.kv_v}) "
          f"+ overhead {fmt_bytes(fit.overhead_bytes)}  = {fmt_bytes(fit.total_bytes)} of {fmt_bytes(fit.budget_bytes)} budget")
    print(f"  context {fit.ctx}{ctx_note}; {where}")
    if meta.context_length and fit.ctx > meta.context_length:
        print(f"  native context is {meta.context_length}: YaRN rope scaling will be enabled")
    for n in fit.notes:
        print(f"  ! {n}")


def _score(fit: Fit) -> tuple:
    """Higher is better: fully on GPU, meets ctx target, better KV precision, bigger model."""
    kv_rank = -list(KV_BYTES).index(fit.kv_v)  # f16 best
    return (fit.full_offload, fit.meets_hermes_ctx, fit.ctx, fit.weights_bytes, kv_rank)


def _threads(hw: HardwareProfile) -> int:
    return max(1, hw.cpu_threads // 2)


# ---------------------------------------------------------------- commands
def cmd_detect(args) -> int:
    hw = detect()
    if args.json:
        print(json.dumps(hw.to_dict(), indent=2))
    else:
        _print_hw(hw)
        budget, label = _budget(hw)
        print(f"\nBudget for a model on {label}: {fmt_bytes(budget)}")
    return 0


def cmd_list(args) -> int:
    hw = detect()
    budget, label = _budget(hw)
    print(f"Fit against {label}, budget {fmt_bytes(budget)}:\n")
    rows = []
    for p in all_presets():
        fit = best_fit(p.meta(), budget)
        verdict = "green" if fit.full_offload and fit.meets_hermes_ctx else "amber" if fit.full_offload else "red"
        rows.append((verdict, p, fit))
    width = max(len(p.id) for _, p, _ in rows)
    for verdict, p, fit in rows:
        kv = f"{fit.kv_k}/{fit.kv_v}"
        dev = "GPU" if _has_gpu(hw) else "RAM"
        place = dev if fit.full_offload else (f"GPU {fit.ngl}L+RAM" if fit.ngl and dev == "GPU" else "too big")
        print(f"[{verdict:>5}] {p.id:<{width}}  {fmt_bytes(p.approx_bytes + p.mmproj_bytes):>9}  ctx {fit.ctx:>6}  kv {kv:<11} {place:<14} {p.notes}")
    dev = "the GPU" if _has_gpu(hw) else "RAM"
    print(f"\ngreen = whole model + 64k context on {dev} · amber = fits with a smaller context · red = does not fit / spills")
    print("Apply one with:  mik model use <id> --apply      Any other GGUF:  mik model use hf:owner/repo[:file] --apply")
    return 0


def cmd_recommend(args) -> int:
    hw = detect()
    _print_hw(hw)
    budget, label = _budget(hw)
    print(f"\nBudget on {label}: {fmt_bytes(budget)}\n")
    best: tuple[Preset, Fit] | None = None
    want_vision = bool(getattr(args, "vision", False))
    for p in [p for p in all_presets() if p.source == "mikronous" and p.vision == want_vision]:
        fit = best_fit(p.meta(), budget)
        if fit.full_offload and (best is None or _score(fit) > _score(best[1])):
            best = (p, fit)
    if best is None:  # nothing fits fully: smallest preset, partial
        p = all_presets()[0]
        best = (p, best_fit(p.meta(), budget))
    preset, fit = best
    _print_fit(f"Recommended: {preset.label}  [{preset.id}]  {preset.repo}", fit, preset.meta(), _has_gpu(hw))
    if not args.apply:
        print(f"\nApply it:  mik model use {preset.id} --apply" + ("" if want_vision else "\n(a model that sees your screen: mik model recommend --vision)"))
        return 0
    return _apply_preset(preset, hw, fit, args)


def _apply_preset(preset: Preset, hw: HardwareProfile, fit: Fit, args) -> int:
    repo, filename, size = apply_mod.resolve_hf(f"hf:{preset.repo}", preset.file_hint)
    meta = preset.meta(size)
    fit = best_fit(meta, fit.budget_bytes)
    return _finish_apply(meta, fit, repo, filename, hw, args, mmproj=_preset_mmproj(preset))


def _preset_mmproj(preset: Preset | None) -> tuple[str, str] | None:
    """(repo, filename) of the vision projector for a vision preset, else None."""
    if preset is None or not preset.vision:
        return None
    name, _size = apply_mod.resolve_mmproj(preset.repo, preset.mmproj)
    return preset.repo, name


def _sync_vision_config(vision: bool, restart: bool = True) -> None:
    """Tell Hermes whether the local model sees images (model.supports_vision + agent.image_input_mode)."""
    try:
        from mikronous_cli import privacy
        cfg = privacy.load_config()
        if not cfg:
            return
        model = cfg.setdefault("model", {}) or {}
        cfg["model"] = model
        agent = cfg.setdefault("agent", {}) or {}
        cfg["agent"] = agent
        if bool(model.get("supports_vision")) == vision and agent.get("image_input_mode") == ("native" if vision else "auto"):
            return
        model["supports_vision"] = vision
        agent["image_input_mode"] = "native" if vision else "auto"
        privacy.save_config(cfg)
        print(f"profile config: supports_vision={'true' if vision else 'false'}")
        if restart:
            from mikronous_cli.platform import hermes_bin
            from mikronous_cli.paths import PROFILE
            subprocess.run([hermes_bin(), "-p", PROFILE, "gateway", "restart"], capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001 - the model still works; vision routing is a nicety
        print(f"(could not update the profile's vision setting: {exc})", file=sys.stderr)


def cmd_sync_config(args) -> int:
    env = LlamaEnv.load()
    _sync_vision_config(bool(env["LLAMA_MMPROJ"].strip()), restart=not args.no_restart)
    print(f"vision: {'on (' + env['LLAMA_MMPROJ'] + ')' if env['LLAMA_MMPROJ'].strip() else 'off'}")
    return 0


def _finish_apply(meta: GGUFMeta, fit: Fit, repo: str | None, filename: str | None, hw: HardwareProfile, args,
                  mmproj: tuple[str, str] | None = None) -> int:
    mmproj_path: Path | None = None
    if repo and filename:
        path = apply_mod.download(repo, filename)
        if mmproj:
            mmproj_path = apply_mod.download(mmproj[0], mmproj[1])
        real = read_meta(str(path))
        if real.block_count:
            if mmproj_path:
                real.file_bytes += mmproj_path.stat().st_size
            meta = real
            fit = best_fit(meta, fit.budget_bytes, target_ctx=getattr(args, "ctx", None) or HERMES_MIN_CTX)
    else:
        path = Path(meta.path)
        if getattr(args, "mmproj", None):
            mmproj_path = Path(args.mmproj).expanduser()
    env = LlamaEnv.load()
    if not _has_gpu(hw):
        fit.ngl = 0
    env.apply_fit(fit, meta, path, threads=_threads(hw), mmproj_path=mmproj_path)
    _sync_vision_config(mmproj_path is not None, restart=False)      # the server restart below reloads nothing in Hermes; gateway picks it up on its next restart
    if getattr(args, "kv", None):
        k, _, v = args.kv.partition("/")
        env["LLAMA_KV_K"], env["LLAMA_KV_V"] = k, (v or k)
    if getattr(args, "ngl", None) is not None:
        env["LLAMA_NGL"] = args.ngl
    if not env["LLAMA_SERVER"]:
        print("LLAMA_SERVER is not set in llama.env; run scripts/install.sh first", file=sys.stderr)
        return 1
    env.save()
    print(f"\nwrote {env.path}\n{env.summary()}\n")
    if getattr(args, "no_restart", False):
        return 0
    return 0 if apply_mod.restart_and_wait(env["LLAMA_PORT"]) else 1


def cmd_use(args) -> int:
    hw = detect()
    budget, label = _budget(hw)
    target_ctx = args.ctx or HERMES_MIN_CTX
    spec: str = args.model
    preset = find_preset(spec)
    repo = filename = None
    if preset:
        repo, filename, size = apply_mod.resolve_hf(f"hf:{preset.repo}", preset.file_hint)
        meta = preset.meta(size)
        try:  # exact header beats preset guesses
            meta = read_meta(hf_resolve_url(repo, filename))
        except Exception as exc:  # noqa: BLE001 - fall back to preset facts
            print(f"(could not read remote header: {exc}; using preset estimates)", file=sys.stderr)
    elif spec.startswith("hf:"):
        repo, filename, size = apply_mod.resolve_hf(spec, args.quant)
        meta = read_meta(hf_resolve_url(repo, filename))
    else:
        path = Path(spec).expanduser()
        if not path.exists():
            print(f"not a preset id, hf:repo, or file: {spec}", file=sys.stderr)
            return 2
        meta = read_meta(str(path))
    print(f"Model   {meta.name or meta.path}  arch={meta.arch} layers={meta.block_count} kv_heads={meta.head_count_kv} "
          f"head_dim={meta.head_dim} native_ctx={meta.context_length}{' MoE' if meta.is_moe else ''}")
    print(f"Budget  {fmt_bytes(budget)} on {label}\n")
    if args.kv:
        k, _, v = args.kv.partition("/")
        fit = estimate(meta, target_ctx, k, v or k, budget)
    else:
        fit = best_fit(meta, budget, target_ctx=target_ctx)
    if args.ngl is not None:
        fit.ngl = args.ngl
    _print_fit("Fit", fit, meta, _has_gpu(hw))
    if not args.apply:
        print("\nAdd --apply to download (if needed), write llama.env and restart the server.")
        return 0
    return _finish_apply(meta, fit, repo, filename, hw, args, mmproj=_preset_mmproj(preset))


def cmd_tune(args) -> int:
    env = LlamaEnv.load()
    changed = False
    for key, val in (("LLAMA_CTX", args.ctx), ("LLAMA_NGL", args.ngl), ("LLAMA_THREADS", args.threads),
                     ("LLAMA_PARALLEL", args.parallel), ("LLAMA_EXTRA_ARGS", args.extra)):
        if val is not None:
            env[key] = val; changed = True
    if args.kv:
        k, _, v = args.kv.partition("/")
        env["LLAMA_KV_K"], env["LLAMA_KV_V"] = k, (v or k); changed = True
    if args.backend:
        script = REPO_DIR / "scripts" / "install-llama.sh"
        res = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                             env={**os.environ, "MIKRONOUS_LLAMA_BACKEND": args.backend, "MIKRONOUS_LLAMA_REINSTALL": "1"})
        sys.stderr.write(res.stderr)
        if res.returncode != 0:
            return res.returncode
        env["LLAMA_SERVER"] = res.stdout.strip().splitlines()[-1]; changed = True
    if not changed:
        print(env.summary())
        print("\nNothing to change. Flags: --ctx --kv K[/V] --ngl --threads --parallel --extra '...' --backend cuda|vulkan|cpu")
        return 0
    env.save()
    print(f"wrote {env.path}\n{env.summary()}\n")
    if args.no_restart:
        return 0
    return 0 if apply_mod.restart_and_wait(env["LLAMA_PORT"]) else 1


def cmd_status(args) -> int:
    env = LlamaEnv.load()
    print(f"{LLAMA_ENV}\n{env.summary()}\n")
    model = Path(env["LLAMA_MODEL"]).expanduser()
    if model.exists():
        meta = read_meta(str(model))
        hw = detect()
        budget, label = _budget(hw)
        fit = estimate(meta, int(env["LLAMA_CTX"]), env["LLAMA_KV_K"], env["LLAMA_KV_V"], budget)
        _print_fit(f"Current fit on {label}", fit, meta, _has_gpu(hw))
    else:
        print("model file missing")
    print(f"\nservice {apply_mod.UNIT}: {'active' if _llama_running() else 'not running'}")
    return 0


def cmd_bench(args) -> int:
    env = LlamaEnv.load()
    print("running a ~1.5k-token prompt + 96 generated tokens ...", file=sys.stderr)
    try:
        r = bench_mod.run(env["LLAMA_PORT"], gen_tokens=args.tokens)
    except Exception as exc:  # noqa: BLE001
        print(f"bench failed: {exc}", file=sys.stderr)
        return 1
    print(f"prompt processing  {r['prompt_tps']:>8} tok/s   ({r['prompt_tokens']} tokens)")
    print(f"generation         {r['gen_tps']:>8} tok/s   ({r['completion_tokens']} tokens)")
    print(f"wall time          {r['wall_s']:>8} s")
    if "vram_used_mib" in r:
        print(f"VRAM in use        {r['vram_used_mib']:>8} / {r['vram_total_mib']} MiB")
    return 0


# ---------------------------------------------------------------- parser
def build_parser(prog: str = "mik model") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description="Fit the local model to this machine's hardware")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="show GPU / RAM / CPU and the memory budget"); d.add_argument("--json", action="store_true"); d.set_defaults(func=cmd_detect)
    sub.add_parser("list", help="presets with a fit verdict for this machine").set_defaults(func=cmd_list)
    r = sub.add_parser("recommend", help="pick the best preset for this hardware"); r.add_argument("--apply", action="store_true", help="download + write llama.env + restart"); r.add_argument("--no-restart", action="store_true")
    r.add_argument("--vision", action="store_true", help="pick a model that sees images (screen questions)"); r.set_defaults(func=cmd_recommend)

    u = sub.add_parser("use", help="fit a preset id, hf:owner/repo[:file], or a local .gguf")
    u.add_argument("model"); u.add_argument("--apply", action="store_true"); u.add_argument("--no-restart", action="store_true")
    u.add_argument("--ctx", type=int, help=f"context target (default {HERMES_MIN_CTX})")
    u.add_argument("--kv", help="KV cache types K[/V], e.g. q8_0 or q8_0/q4_0 or f16")
    u.add_argument("--ngl", type=int, help="force GPU layer count (999 = all)")
    u.add_argument("--quant", help="filename hint when hf:repo has no file (default Q4_K_M)")
    u.add_argument("--mmproj", help="vision projector .gguf for a local multimodal model (presets resolve theirs)")
    u.set_defaults(func=cmd_use)
    sc = sub.add_parser("sync-config", help="write the model's vision capability into the Hermes profile config")
    sc.add_argument("--no-restart", action="store_true"); sc.set_defaults(func=cmd_sync_config)

    t = sub.add_parser("tune", help="change settings of the current model and restart")
    t.add_argument("--ctx", type=int); t.add_argument("--kv"); t.add_argument("--ngl", type=int); t.add_argument("--threads", type=int)
    t.add_argument("--parallel", type=int); t.add_argument("--extra", help="raw extra llama-server args")
    t.add_argument("--backend", choices=["cuda", "cuda-13.4", "cuda-12.8", "vulkan", "cpu", "cuda-build"])
    t.add_argument("--no-restart", action="store_true"); t.set_defaults(func=cmd_tune)

    sub.add_parser("status", help="current settings and whether they fit").set_defaults(func=cmd_status)
    b = sub.add_parser("bench", help="prompt + generation tokens/s on the running server"); b.add_argument("--tokens", type=int, default=96); b.set_defaults(func=cmd_bench)
    return p


def main(argv: list[str] | None = None, prog: str = "mik model") -> int:
    args = build_parser(prog).parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
