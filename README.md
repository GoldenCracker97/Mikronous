# Mikronous

A small, local personal assistant for the KDE Plasma desktop. The brain is
[Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research, MIT) with its
full toolset: memory, skills, reminders, files, terminal, web and browser. The model runs on
your own GPU through llama.cpp. Mikronous adds the Linux desktop shell Hermes does not ship:
a tray app, hotkey chat window, desktop notifications, and desktop tools for the agent.

Status: **Phase 0** — profile, plugin stub, local model server, gateway. No tray UI yet.

## What you get

| Piece | What it does | Phase |
|---|---|---|
| Hermes profile `mikronous` | Own persona (`SOUL.md`), memory, config; full Hermes toolset + the web (keyless DuckDuckGo) | 0 |
| `mikronous-llama.service` | llama.cpp server on `:8081`, tuned for ~8 GB VRAM by default | 0 |
| `mik model` | Detect your hardware, pick/tune model, quant, context, KV cache; works for any GGUF | 0.5 |
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

The installer creates the Hermes profile, links the plugin, builds or downloads
`llama-server` (CUDA if `nvcc` is present, otherwise Vulkan), downloads the default model
(Qwen3-4B-Instruct Q4_K_M, ~2.5 GB) into `~/.hermes/models`, and starts two user services:
`mikronous-llama` and `hermes-gateway-mikronous`.

Already running your own llama-server on `:8081`? Use `scripts/install.sh --no-model`.

## Commands

| Command | Purpose |
|---|---|
| `mikronous chat` | Talk to the assistant in the terminal (Hermes profile command) |
| `mikronous gateway status` | Gateway (API server + cron) state |
| `mik doctor` | One-screen health check of every piece |
| `systemctl --user restart mikronous-llama` | Restart the model server after editing `~/.config/mikronous/llama.env` |

`mikronous` is the Hermes profile command (created by `hermes profile create mikronous`);
`mik` is this project's own CLI.

## Tuning the model by hand (until `mik model` lands)

Edit `~/.config/mikronous/llama.env`:

- `LLAMA_MODEL`: any GGUF path. `scripts/fetch-model.sh owner/repo file.gguf` downloads one.
- `LLAMA_CTX`: context tokens. Hermes wants at least 65536.
- `LLAMA_KV_K` / `LLAMA_KV_V`: `f16` (best), `q8_0`, `q4_0` (smallest). Memory for the KV cache roughly halves at each step.
- `LLAMA_NGL`: layers on the GPU; lower it to spill into system RAM when the model does not fit.

Then `systemctl --user restart mikronous-llama`.

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
