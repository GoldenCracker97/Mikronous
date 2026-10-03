# Changelog

All notable changes to Mikronous are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Network tools**: `lan_devices` (ARP/neighbour table with optional ping sweep and DNS/mDNS names),
  `host_check` (ping + TCP ports), `wake_on_lan` (magic packet); Linux and Windows.
- **`http_request`**: generic HTTP/JSON client for any API; keys referenced by name from the profile `.env`
  (`auth_env`), injected as a header and redacted from results; writes require `confirm: true`.
- **Skills**: `lan`, `remote-command` (SSH through the terminal with the user's keys), `home-assistant`
  (Hermes's built-in tools once `HASS_URL`/`HASS_TOKEN` are set), `api-calls`, `skill-authoring` (offer to save
  taught procedures with `skill_manage`).
- `mik doctor`: Home Assistant row when configured. `mik privacy`: service keys listed separately from provider keys.
- **Tray: past-chats pane** (`CHATS` button, `Ctrl+H`, `mik chats`): earlier tray sessions from the gateway, open,
  rename or delete them.
- **Tray: Settings dialog** (`SETTINGS` button, `Ctrl+,`, `mik settings`, tray menu): voice, approval mode, internet
  on/off, notes folder, file-search folders, boot litany, keep-model-on-quit, Windows hotkey. Gateway-side changes
  restart the gateway automatically.
- **Tray: Update button**: checks GitHub on demand, offers to apply `mik update`; a quiet background check marks the
  button when new commits exist.
- **Tray: drop files or folders** onto the window to attach their paths to the next message.
- **Voice input and output** (`[voice]` extra, `install.sh --voice` / `install.ps1 -VoiceInput`): hold VOX or tap
  `Meta+Shift+V` (`Ctrl+Alt+V`), faster-whisper transcribes locally (size in Settings); optional Piper read-aloud.
  Linux records with pw-record/parecord/arecord, Windows with sounddevice. `mik doctor` voice row.
- **KRunner plugin** (Linux): `mik <question>` in Alt+Space; the tray serves `org.kde.krunner1` with dbus-fast, answers
  arrive as notifications when the window is hidden; `mik tray --install-krunner` writes the plugin file (installer does it).
- **Desktop control tools** `media_control` (MPRIS play/pause/next/status) and `system_control` (volume, brightness,
  do-not-disturb, lock, focus a window) with a `desktop-control` skill; Linux and Windows.
- **Selected-text actions** (`Meta+Shift+Space`, Windows `Ctrl+Alt+Shift+Space`, `mik selection`): Explain, Summarise,
  Rewrite, Translate, Ask about it; Rewrite/Translate answers land on the clipboard. Second Desktop Action in the
  `.desktop` file and kglobalshortcutsrc; second `RegisterHotKey` on Windows; both keys in Settings.
- **Routines** (Settings → Routines, tray menu, `mik routines`, `mik routine` CLI): scheduled agent tasks through the
  gateway's cron API with presets (morning briefing, watch a page, weekly notes review); pause, run now, edit, delete;
  reminders listed alongside.

### Fixed
- `lan_devices` crashed on IPv6 neighbours in `ip neigh` output.

## [0.1.1] - 2026-10-03

Fixes and additions from the first days of real use, plus native Windows support.

### Added
- **Weather skill**: hourly or daily forecasts for any place from the National Weather Service (US) or
  Open-Meteo (elsewhere), free and keyless, through a bundled script; no browser scraping.
- **Windows support**: `scripts\install.ps1` installs everything natively on Windows 10/11 (no admin):
  Hermes Agent when missing, the profile, plugin junctions, `mik`, a prebuilt llama.cpp (CUDA 13.4 /
  CUDA 12.4 / Vulkan / CPU), the model, the gateway scheduled task, the tray with a `Ctrl+Alt+Space`
  hotkey and Run-key autostart. Desktop tools use Windows toasts, `startfile` and PowerShell's clipboard;
  reminders reach the tray over a named pipe. CI runs the test-suite on Windows too.
- **Hermes auto-install**: both installers run Hermes's own installer (non-interactive) when `hermes`
  is not found. `MIKRONOUS_SKIP_HERMES_INSTALL=1` opts out.
- `mik model start|stop|restart|running` to control the model server on either OS.
- `mik update` (and "Update Mikronous…" in the tray menu): fast-forward pull from GitHub, re-run the
  installer without the model step, restart the tray. `--check` only reports, `--pull` skips the reinstall.

### Fixed
- `web_extract` failed with "DuckDuckGo is a search-only backend": Hermes auto-detects DuckDuckGo once its
  package is installed, leaving page extraction without a backend. The profile now pins DuckDuckGo for
  search and Exa's keyless tier for extraction.

### Changed
- The model server is managed through `mikronous_model.runner` (systemd on Linux, a detached process with a
  pid file on Windows); `mik doctor`, `mik model` and the tray all use it.
- Reminder scripts are Python files instead of shell scripts, so Hermes cron runs them on every OS.

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

[0.1.1]: https://github.com/GoldenCracker97/Mikronous/releases/tag/v0.1.1
[0.1.0]: https://github.com/GoldenCracker97/Mikronous/releases/tag/v0.1.0
