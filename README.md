# Mikronous

A small, local personal assistant for the KDE Plasma desktop. The brain is
[Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research, MIT) with its
full toolset: memory, skills, reminders, files, terminal, web and browser. The model runs on
your own GPU through llama.cpp. Mikronous adds the Linux desktop shell Hermes does not ship:
a tray app, hotkey chat window, desktop notifications, and desktop tools for the agent.

Status: **Phase 2** — profile, local model server, gateway, hardware-fit tool, desktop tools, reminders as notifications. No tray UI yet.

## What you get

| Piece | What it does | Phase |
|---|---|---|
| Hermes profile `mikronous` | Own persona (`SOUL.md`), memory, config; full Hermes toolset + the web (keyless DuckDuckGo) | 0 |
| `mikronous-llama.service` | llama.cpp server on `:8081`, tuned for ~8 GB VRAM by default | 0 |
| `mik model` | Detect your hardware, pick/tune model, quant, context, KV cache; works for any GGUF | 0.5 ✓ |
| Hermes plugin `mikronous` | Tools: `desktop_notify`, `desktop_open`, `clipboard`, `notes_manage`, `docs_search`, `set_reminder`; skills `daily-briefing`, `file-qa`; `/notes` | 1 ✓ |
| `mikronous` platform | Reminders from Hermes cron arrive as KDE notifications (and in `~/.local/share/mikronous/inbox.jsonl`) | 2 ✓ |
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

**Gateway topology.** The Mikronous profile runs its own gateway process
(`hermes-gateway-mikronous.service`, `gateway.standalone: true` in its config) with its API server on
`http://127.0.0.1:8643/v1`, authenticated with the `API_SERVER_KEY` from
`~/.hermes/profiles/mikronous/.env`. Hermes 0.21+ would otherwise fold every profile into one shared
host gateway, but that host did not load this profile's plugin in testing (no desktop tools, no
reminder delivery), so the profile stays standalone. Your default profile's gateway is untouched.
`mik doctor` prints the live endpoint.

## What leaves your machine

| | |
|---|---|
| Model, memory, notes, documents, chat history | Never leave. The model is pinned to `llama-server` on `127.0.0.1:8081`; Hermes side tasks (summaries, titles) use the same model or are skipped. |
| Web search and page extraction | Go out, like a browser would: DuckDuckGo for search, Hermes's keyless free tiers (Exa, Parallel, Firecrawl, Keenable) for page text. No account, no key, no identifiers. `mik privacy offline` turns both off. |
| Paid services | Nothing can enrol you. Toolsets that only work with paid keys (`image_gen`, `video_gen`, `tts`, `x_search`, `vision`) and the Nous-managed `connections` toolset are disabled in the profile, Claude Code / Codex login borrowing is off, telemetry is off, and no fallback providers are configured. Nous Portal is only ever enabled by running `hermes setup --portal` yourself. |

`mik privacy status` prints the live state of each line above; `mik doctor` includes it as the `local-only` row.

## Commands

| Command | Purpose |
|---|---|
| `mikronous chat` | Talk to the assistant in the terminal (Hermes profile command) |
| `mikronous gateway status` | Gateway (API server + cron) state |
| `mik ask "…"` | One question through the gateway with the full toolset (`--session <id>` to continue) |
| `mik doctor` | One-screen health check of every piece |
| `mik tools` | Toolsets the gateway exposes to the assistant |
| `mik privacy status\|offline\|online` | What can leave the machine; switch web access off or on |
| `mik docs status\|reindex\|search <q>` | Document index used by `docs_search` |
| `systemctl --user restart mikronous-llama` | Restart the model server after editing `~/.config/mikronous/llama.env` |

`mikronous` is the Hermes profile command (created by `hermes profile create mikronous`);
`mik` is this project's own CLI.

## Desktop tools the agent gets

| Tool | What it does | Where things live |
|---|---|---|
| `desktop_notify` | KDE notification via D-Bus (`notify-send` fallback) | |
| `desktop_open` | Open a URL, a file, or launch an installed app by name (`firefox`, `dolphin`) | `.desktop` files in the usual XDG dirs |
| `clipboard` | Read / set the clipboard (Klipper via `qdbus6`, `wl-paste`/`xclip` fallback) | |
| `set_reminder` | "Remind me in 20 minutes to…": schedules a Hermes cron job in no-agent mode whose output is delivered as a desktop notification; `list` / `cancel` too | jobs in `mikronous cron list`; scripts in the profile's `scripts/` |
| `notes_manage` | Durable notes and to-dos: add, list, search, done, update, delete; also `/notes` in chat | `~/Mikronous/notes/*.md` (one file per note, plain markdown you can edit) |
| `docs_search` | Full-text search over your document folders, then the agent reads the hit with `read_file` | index in `~/.local/share/mikronous/docs.sqlite`; folders from `MIKRONOUS_DOCS_DIRS` (default `~/Documents`) |

**Reminders.** "Remind me in 20 minutes to …" or "every weekday at 9 …" makes the agent call
`set_reminder`, which creates a Hermes cron job (no model involved when it fires) delivered to the
`mikronous` platform: a KDE notification, plus a line in
`~/.local/share/mikronous/inbox.jsonl` (and the tray window once Phase 3 lands) so nothing is
lost while you are away. `mikronous cron list` shows the jobs. Reminders are created from chat
sessions (the tray, `mikronous chat`, or `mik ask "…"`); Hermes hides the scheduling tool in
`mikronous -z` one-shots by design.

`mik docs status | reindex | search <q>` manages the document index from the terminal. PDFs and
Office files are extracted with Hermes's own converter when indexed from inside a Hermes session.
Two skills ship with the plugin: `mikronous:daily-briefing` and `mikronous:file-qa`.

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
