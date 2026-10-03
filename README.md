# Mikronous

A small, local personal assistant for the KDE Plasma desktop. The brain is
[Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research, MIT) with its
full toolset: memory, skills, reminders, files, terminal, web and browser. The model runs on
your own GPU through llama.cpp. Mikronous adds the Linux desktop shell Hermes does not ship:
a tray app, hotkey chat window, desktop notifications, and desktop tools for the agent.

Status: **Phase 0.5** — profile, local model server, gateway, hardware-fit tool. No tray UI yet.

## What you get

| Piece | What it does | Phase |
|---|---|---|
| Hermes profile `mikronous` | Own persona (`SOUL.md`), memory, config; full Hermes toolset + the web (keyless DuckDuckGo) | 0 |
| `mikronous-llama.service` | llama.cpp server on `:8081`, tuned for ~8 GB VRAM by default | 0 |
| `mik model` | Detect your hardware, pick/tune model, quant, context, KV cache; works for any GGUF | 0.5 ✓ |
| Hermes plugin `mikronous` | Tools: `desktop_notify`, `desktop_open`, `clipboard`, `notes_manage`, `docs_search` | 1 |
| `mikronous` platform | Reminders from Hermes cron arrive as KDE notifications | 2 |
| Tray app | Hotkey (`Meta+Space`) chat window, streaming, approval cards | 3 |

## Install (Linux, KDE Plasma)

1. Install Hermes Agent: `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`
2. Clone this repo and run the installer:

```bash
git clone https://github.com/GoldenCracker97/Mikronous && cd Mikronous
scripts/install.sh
mik doctor
```

The installer creates the Hermes profile, links the plugin, installs the `mik` CLI, downloads a
prebuilt `llama-server` from the llama.cpp nightly releases, downloads the default model
(Qwen3-4B-Instruct Q4_K_M, ~2.5 GB) into `~/.hermes/models`, and starts two user services:
`mikronous-llama` and `hermes-gateway-mikronous`.

The llama.cpp backend is picked from your hardware, no CUDA toolkit needed:

| Hardware | Backend | How it is chosen |
|---|---|---|
| NVIDIA, driver CUDA ≥ 13.4 | `cuda-13.4` prebuilt | `nvidia-smi` reports the driver's CUDA version |
| NVIDIA, driver CUDA ≥ 12.8 | `cuda-12.8` prebuilt | same |
| NVIDIA with an older driver, AMD, Intel | `vulkan` prebuilt | GPU present without a usable CUDA driver |
| No GPU | `cpu` prebuilt | |

Override with `MIKRONOUS_LLAMA_BACKEND=cuda|vulkan|cpu|cuda-build` (the last compiles from source
and needs `nvcc`), pin a nightly with `MIKRONOUS_LLAMA_TAG=b11146`, or force a fresh install
with `MIKRONOUS_LLAMA_REINSTALL=1`.

Already running your own llama-server on `:8081`? Use `scripts/install.sh --no-model`.

**Gateway topology.** Hermes 0.21+ runs one host gateway (`hermes-gateway.service`, your default
profile) that serves every profile. The installer enables the API server on it and the Mikronous
endpoint becomes `http://127.0.0.1:8642/p/mikronous/v1`, authenticated with the `API_SERVER_KEY`
from `~/.hermes/profiles/mikronous/.env`. On older Hermes the profile gets its own
`hermes-gateway-mikronous.service` and plain `http://127.0.0.1:8642/v1`. `mik doctor` prints
whichever applies.

## Commands

| Command | Purpose |
|---|---|
| `mikronous chat` | Talk to the assistant in the terminal (Hermes profile command) |
| `mikronous gateway status` | Gateway (API server + cron) state |
| `mik doctor` | One-screen health check of every piece |
| `systemctl --user restart mikronous-llama` | Restart the model server after editing `~/.config/mikronous/llama.env` |

`mikronous` is the Hermes profile command (created by `hermes profile create mikronous`);
`mik` is this project's own CLI.

## Fit the model to your hardware: `mik model`

```bash
mik model detect                 # GPU / VRAM / RAM / CPU and the memory budget
mik model list                   # presets with a green / amber / red verdict for this machine
mik model recommend --apply      # pick the best preset, download, write settings, restart
mik model use qwen3-8b --apply   # a specific preset
mik model use hf:unsloth/Qwen3-14B-GGUF --apply          # any Hugging Face GGUF (Q4_K_M picked)
mik model use hf:owner/repo:file.gguf --ctx 32768 --apply # exact file + context override
mik model use ~/models/anything.gguf --apply              # a local file
mik model tune --kv f16 --ctx 65536                       # change KV cache / context and restart
mik model tune --backend vulkan                           # switch llama.cpp build (cuda|vulkan|cpu)
mik model status                 # current settings and whether they still fit
mik model bench                  # prompt + generation tokens/s, VRAM in use
```

How the fit works: weights (file size) + KV cache (context × layers × KV heads × head size × bytes
per element) + runtime overhead must fit the free VRAM minus 512 MiB headroom. The tool keeps the
64k context Hermes needs by quantising the KV cache first (`f16` → `q8_0` → `q8_0/q4_0` → `q4_0`),
only then shrinks the context, and only then spills layers to system RAM (`LLAMA_NGL`). Models
with a shorter native context get YaRN rope scaling automatically. Everything lands in
`~/.config/mikronous/llama.env`, which you can also edit by hand, then
`systemctl --user restart mikronous-llama`.

Hermes's own local-model catalog (27B+ models) shows up in `mik model list` too when Hermes is
installed from git.

## Layout

```
profile/            Hermes profile template (SOUL.md, config.yaml, env.example)
hermes_plugin/      The `mikronous` Hermes plugin (tools + delivery platform)
mikronous_cli/      `mik` CLI (doctor, model, toggle)
mikronous_model/    hardware-fit library used by `mik model` and the tray (Phase 0.5)
mikronous_tray/     PySide6 tray app (Phase 3)
skills/             Skills shipped with Mikronous
systemd/            User units + env template
scripts/            install.sh, install-llama.sh, fetch-model.sh
```

## License

MIT.
