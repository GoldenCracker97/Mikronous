# Changelog

All notable changes to Mikronous are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [0.1.0] - 2026-10-03

First release. A local personal assistant for the KDE Plasma desktop, with Hermes Agent as the brain
and llama.cpp on your own GPU.

### Added
- **Hermes profile `mikronous`**: persona (`SOUL.md`), full Hermes toolset, keyless web search, long-term
  memory, and local-only guarantees (no fallback providers, no borrowed logins, telemetry off, paid-key
  toolsets disabled). `mik privacy status|offline|online`.
- **Local model server**: `mikronous-llama.service` running a prebuilt llama.cpp (`cuda-12.8`, `cuda-13.4`,
  `vulkan` or `cpu`, chosen from the hardware) on `127.0.0.1:8081`.
- **`mik model`**: detect GPU/VRAM/RAM, list presets with a fit verdict, recommend/use/tune any GGUF
  (Hugging Face or local), bench. Keeps the 64k context Hermes wants by quantising the KV cache first.
- **Hermes plugin**: desktop tools `desktop_notify`, `desktop_open`, `clipboard`, `notes_manage`,
  `docs_search` (FTS5 over your document folders), `set_reminder`; `/notes` command; skills
  `daily-briefing` and `file-qa`; a `mikronous` delivery platform so cron reminders arrive as KDE
  notifications and in the tray window.
- **Tray app** (`mik tray`): `Meta+Space` chat window on the Hermes runs API with streamed replies, tool
  activity, approval cards, Stop, New chat, sessions that survive restarts, and a reminder inbox socket.
  Installer registers the shortcut for both Plasma 5 and Plasma 6 and an autostart entry.
- **Machine Cult theme**: data-slate window (iron, brass, Martian red, phosphor green), original
  cog-and-circuit icons, bundled OFL fonts, boot litany, and persona voice levels (`mik voice
  plain|light|full`).
- **Model lifecycle**: the tray loads the model on launch and unloads it on Quit (or via the tray
  menu), so quitting frees the VRAM; `MIKRONOUS_KEEP_MODEL=1` opts out.
- **`mik doctor`**: one-screen health check of every piece, including tray and shortcut state.
- **`mik ask`, `mik tools`, `mik docs`**: terminal access to the gateway, toolsets and document index.

### Known limitations
- Verified on KDE Plasma 5.27 (X11) with an NVIDIA RTX 4070 Ti; Plasma 6 shortcut registration is
  implemented but untested.
- With the default `approvals.mode: smart`, the approval card only appears when Hermes's guardian pass
  escalates or denies; `mikronous config set approvals.mode manual` asks every time.
- Small models (8B and under) occasionally omit a field in a tool call; `set_reminder` tolerates the
  common cases. If the full machine-priest voice makes your model skip tool calls, use `mik voice light`.
- `mikronous -z` one-shots cannot create reminders (Hermes hides scheduling there); use the tray,
  `mikronous chat`, or `mik ask`.

### Requirements
- Linux with KDE Plasma (5.27 or 6), systemd user session, Python 3.11+.
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) 0.21 or newer.
- A GPU with 8 GB or more of VRAM recommended (NVIDIA via CUDA prebuilts; AMD/Intel via Vulkan);
  CPU-only works with small models.

[0.1.0]: https://github.com/GoldenCracker97/Mikronous/releases/tag/v0.1.0
