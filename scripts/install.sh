#!/usr/bin/env bash
# Mikronous installer: Hermes profile + plugin + mik CLI + local llama-server + gateway service + tray app.
# Idempotent: re-running updates config in place and never overwrites secrets, memories, or an edited SOUL.md.
#
#   scripts/install.sh            # everything
#   scripts/install.sh --no-model # skip model download / llama-server (already have one on :8081)
#   scripts/install.sh --no-tray  # skip the tray app (PySide6) and the Meta+Space shortcut
#   scripts/install.sh --voice    # also install local voice input/output (faster-whisper + Piper); remembered for mik update
#
# Afterwards: `mik doctor` shows what is running.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="mikronous"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PROFILE_HOME="$HERMES_HOME/profiles/$PROFILE"
CONF_DIR="$HOME/.config/mikronous"
UNIT_DIR="$HOME/.config/systemd/user"
MODEL_FILE="${MIKRONOUS_MODEL_FILE:-Qwen3-4B-Instruct-2507-Q4_K_M.gguf}"
WITH_MODEL=1
WITH_TRAY=1
# --voice is remembered (marker file) so `mik update`, which re-runs this script, keeps the voice extras.
VOICE_MARKER="${XDG_CONFIG_HOME:-$HOME/.config}/mikronous/voice.enabled"
WITH_VOICE="${MIKRONOUS_VOICE_INPUT:-$([[ -f "$VOICE_MARKER" ]] && echo 1 || echo 0)}"
# The tray choice is remembered the same way, so `mik update` (which passes --no-tray to skip the desktop
# setup) still reinstalls mik WITH the PySide6 extra instead of silently dropping it.
TRAY_MARKER="${XDG_CONFIG_HOME:-$HOME/.config}/mikronous/tray.enabled"
FAILURES=()
for arg in "$@"; do
  case "$arg" in
    --no-model) WITH_MODEL=0 ;;
    --no-tray) WITH_TRAY=0 ;;
    --voice) WITH_VOICE=1 ;;
    --no-voice) WITH_VOICE=0; rm -f "$VOICE_MARKER" ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

CURRENT_STEP="start"
step() { CURRENT_STEP="$*"; printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { echo "   !! $*" >&2; FAILURES+=("$CURRENT_STEP: $*"); }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }; }
hp() { hermes -p "$PROFILE" "$@"; }   # profile-scoped hermes; works before ~/.local/bin is on PATH
sha() { sha256sum "$1" | cut -d' ' -f1; }

need systemctl
need curl

# ---------------------------------------------------------------------------
step "Hermes Agent"
# Hermes is the brain; install it with its own installer when it is missing. --non-interactive skips
# the stages that need input (model/provider setup, gateway wizard): Mikronous configures its own
# profile below, so nothing is lost. MIKRONOUS_SKIP_HERMES_INSTALL=1 turns this off.
export PATH="$HOME/.local/bin:$PATH"
if command -v hermes >/dev/null 2>&1; then
  echo "found: $(command -v hermes) ($(hermes --version 2>/dev/null | head -1 || echo version unknown))"
elif [[ "${MIKRONOUS_SKIP_HERMES_INSTALL:-0}" == 1 ]]; then
  echo "hermes not found and MIKRONOUS_SKIP_HERMES_INSTALL=1; install it: curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash" >&2; exit 1
else
  echo "hermes not found; installing Hermes Agent (source install under ~/.hermes, launcher in ~/.local/bin)"
  if curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive; then
    hash -r
    command -v hermes >/dev/null 2>&1 || { echo "Hermes installed but 'hermes' is not on PATH; open a new terminal and re-run scripts/install.sh" >&2; exit 1; }
    echo "installed: $(command -v hermes)"
  else
    echo "Hermes installer failed; see ~/.hermes/logs/install.log, then re-run scripts/install.sh" >&2; exit 1
  fi
fi

# ---------------------------------------------------------------------------
step "Hermes profile '$PROFILE'"
CREATED_NOW=0
if [[ -d "$PROFILE_HOME" ]] && [[ -e "$PROFILE_HOME/config.yaml" || -e "$PROFILE_HOME/SOUL.md" || -e "$PROFILE_HOME/.env" ]]; then
  echo "profile exists at $PROFILE_HOME"
else
  hermes profile create "$PROFILE" || { echo "hermes profile create failed" >&2; exit 1; }
  CREATED_NOW=1
fi
[[ -d "$PROFILE_HOME" ]] || { echo "expected $PROFILE_HOME after profile create" >&2; exit 1; }
mkdir -p "$PROFILE_HOME/.mikronous"

# SOUL.md: install ours on first install, over Hermes's untouched starter, or over our own
# previous version. Keep anything the user (or the agent) edited.
SOUL_DST="$PROFILE_HOME/SOUL.md"; SOUL_SHA_FILE="$PROFILE_HOME/.mikronous/soul.sha"
install_soul=0; reason=""
if [[ ! -f "$SOUL_DST" || "$CREATED_NOW" == 1 ]]; then install_soul=1; reason="first install"
elif [[ "${MIKRONOUS_FORCE_SOUL:-0}" != 1 && -f "$SOUL_SHA_FILE" && "$(cat "$SOUL_SHA_FILE")" == "$(sha "$SOUL_DST")" \
        && "$(cat "$PROFILE_HOME/.mikronous/soul.src.sha" 2>/dev/null)" == "$(sha "$REPO_DIR/profile/SOUL.md")" ]]; then reason="up to date"
elif head -c 60 "$SOUL_DST" | grep -q "^You are Hermes Agent"; then install_soul=1; reason="replacing Hermes starter"
elif [[ -f "$SOUL_SHA_FILE" && "$(cat "$SOUL_SHA_FILE")" == "$(sha "$SOUL_DST")" ]]; then install_soul=1; reason="updating unmodified Mikronous version"
elif [[ "${MIKRONOUS_FORCE_SOUL:-0}" == 1 ]]; then install_soul=1; reason="forced"
else reason="edited locally; keeping it (MIKRONOUS_FORCE_SOUL=1 to overwrite)"
fi
# Voice level (plain | light | full): keep what the installed SOUL.md already uses, else MIKRONOUS_VOICE (default full).
prev_voice="$(grep -A1 'voice:start' "$SOUL_DST" 2>/dev/null | grep -oiE 'voice: (plain|light|full)' | awk '{print tolower($2)}' | head -1)"
VOICE="${prev_voice:-${MIKRONOUS_VOICE:-full}}"
apply_voice() { # replaces the voice block in $SOUL_DST with profile/voices/$VOICE.md
  python3 - "$SOUL_DST" "$REPO_DIR/profile/voices/$VOICE.md" <<'PY'
import re, sys, pathlib
soul, voice = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
text = soul.read_text(encoding="utf-8"); block = voice.read_text(encoding="utf-8").strip() + "\n"
new = re.sub(r"<!-- voice:start -->\n.*?<!-- voice:end -->", lambda m: f"<!-- voice:start -->\n{block}<!-- voice:end -->", text, count=1, flags=re.DOTALL)
soul.write_text(new, encoding="utf-8")
PY
}
if [[ "$install_soul" == 1 ]]; then
  cp "$REPO_DIR/profile/SOUL.md" "$SOUL_DST"
  apply_voice && reason="$reason, voice: $VOICE"
  sha "$SOUL_DST" > "$SOUL_SHA_FILE"; sha "$REPO_DIR/profile/SOUL.md" > "$PROFILE_HOME/.mikronous/soul.src.sha"
elif [[ -f "$SOUL_SHA_FILE" && "$(cat "$SOUL_SHA_FILE")" == "$(sha "$SOUL_DST")" ]]; then
  reason="$reason (voice: $VOICE)"
fi
echo "SOUL.md: $reason"

# config.yaml: ours is the source of truth; keep a backup of anything that differs.
if [[ "$CREATED_NOW" == 0 && -f "$PROFILE_HOME/config.yaml" ]] && ! cmp -s "$REPO_DIR/profile/config.yaml" "$PROFILE_HOME/config.yaml"; then
  cp "$PROFILE_HOME/config.yaml" "$PROFILE_HOME/config.yaml.bak.$(date +%s)"
  echo "existing config.yaml backed up"
fi
cp "$REPO_DIR/profile/config.yaml" "$PROFILE_HOME/config.yaml"; echo "config.yaml installed"

# .env: add missing keys only; generate the API key once.
touch "$PROFILE_HOME/.env"; chmod 600 "$PROFILE_HOME/.env"
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  key="${line%%=*}"
  grep -q "^${key}=" "$PROFILE_HOME/.env" && continue
  value="${line#*=}"; value="${value%% #*}"; value="${value%"${value##*[![:space:]]}"}"
  if [[ "$key" == "API_SERVER_KEY" ]]; then
    value="$(openssl rand -hex 24 2>/dev/null || head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  echo "${key}=${value}" >> "$PROFILE_HOME/.env"
  echo "set $key"
done < "$REPO_DIR/profile/env.example"

# ---------------------------------------------------------------------------
step "Mikronous plugin"
mkdir -p "$PROFILE_HOME/plugins"
ln -sfn "$REPO_DIR/hermes_plugin/mikronous" "$PROFILE_HOME/plugins/mikronous"
echo "linked $PROFILE_HOME/plugins/mikronous -> $REPO_DIR/hermes_plugin/mikronous"
# config.yaml already lists plugins.enabled: [mikronous]; this records the consent grant too.
hp plugins enable mikronous >/dev/null 2>&1 || echo "(plugins enable skipped; config.yaml enables it anyway)"
# Shipped skills: a `mikronous` category inside the profile's skills dir, so they appear in the
# agent's <available_skills> like any installed skill (Hermes follows symlinks when scanning).
mkdir -p "$PROFILE_HOME/skills"
ln -sfn "$REPO_DIR/skills" "$PROFILE_HOME/skills/mikronous"
echo "linked $PROFILE_HOME/skills/mikronous -> $REPO_DIR/skills"

# ---------------------------------------------------------------------------
step "mik CLI"
install_mik_venv() {
  # No uv/pipx: a private venv under ~/.local/share/mikronous plus a ~/.local/bin/mik symlink.
  local venv="$HOME/.local/share/mikronous/venv"
  python3 -m venv "$venv" >/dev/null 2>&1 || python3 -m venv --without-pip "$venv" >/dev/null 2>&1 || return 1
  if [[ ! -x "$venv/bin/pip" ]]; then
    # Debian/Ubuntu without python3-venv's ensurepip: bootstrap pip into the venv.
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$venv/bin/python" - >/dev/null 2>&1 || return 1
  fi
  "$venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$venv/bin/pip" install --quiet --editable "$REPO_DIR$MIK_EXTRAS" >/dev/null 2>&1 || return 1
  mkdir -p "$HOME/.local/bin" && ln -sfn "$venv/bin/mik" "$HOME/.local/bin/mik"
}
# The tray app needs PySide6 (~150 MB); it is an optional extra so headless installs stay small.
WANT_TRAY="$WITH_TRAY"; [[ -f "$TRAY_MARKER" ]] && WANT_TRAY=1
MIK_EXTRAS=""; [[ "$WANT_TRAY" == 1 ]] && MIK_EXTRAS="[tray]"
[[ "$WANT_TRAY" == 1 && "$WITH_VOICE" == 1 ]] && { MIK_EXTRAS="[tray,voice]"; mkdir -p "$(dirname "$VOICE_MARKER")"; touch "$VOICE_MARKER"; }
if command -v uv >/dev/null 2>&1; then
  (cd "$REPO_DIR" && uv tool install --force --editable ".$MIK_EXTRAS" >/dev/null 2>&1) && echo "installed: mik (uv tool)" || fail "uv tool install failed"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force --editable "$REPO_DIR$MIK_EXTRAS" >/dev/null 2>&1 && echo "installed: mik (pipx)" || fail "pipx install failed"
elif install_mik_venv; then
  echo "installed: mik (venv at ~/.local/share/mikronous/venv -> ~/.local/bin/mik)"
else
  fail "could not install mik (no uv/pipx, venv failed); use: python3 -m mikronous_cli doctor  (from $REPO_DIR)"
fi
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "note: add ~/.local/bin to PATH for 'mik' and 'mikronous' commands" ;; esac

# ---------------------------------------------------------------------------
if [[ "$WITH_MODEL" == 1 ]]; then
  step "Local model + llama-server"
  if LLAMA_BIN="$(bash "$REPO_DIR/scripts/install-llama.sh")"; then
    if bash "$REPO_DIR/scripts/fetch-model.sh"; then
      MODEL_PATH="$HERMES_HOME/models/$MODEL_FILE"
      mkdir -p "$CONF_DIR" "$UNIT_DIR"
      if [[ ! -f "$CONF_DIR/llama.env" ]]; then
        threads=$(( $(nproc) / 2 )); (( threads > 0 )) || threads=1
        sed -e "s|^LLAMA_SERVER=.*|LLAMA_SERVER=$LLAMA_BIN|" \
            -e "s|^LLAMA_MODEL=.*|LLAMA_MODEL=$MODEL_PATH|" \
            -e "s|^LLAMA_THREADS=.*|LLAMA_THREADS=$threads|" \
            "$REPO_DIR/systemd/mikronous-llama.env.example" > "$CONF_DIR/llama.env"
        echo "wrote $CONF_DIR/llama.env"
      else
        # Keep the user's tuning but always point at the binary we just (re)installed.
        sed -i "s|^LLAMA_SERVER=.*|LLAMA_SERVER=$LLAMA_BIN|" "$CONF_DIR/llama.env"
        echo "kept existing $CONF_DIR/llama.env (edit it or run \`mik model\`)"
      fi
      cp "$REPO_DIR/systemd/mikronous-llama.service" "$UNIT_DIR/"
      systemctl --user daemon-reload
      if systemctl --user enable --now mikronous-llama.service; then
        systemctl --user restart mikronous-llama.service
        echo -n "waiting for llama-server on :8081 "
        up=0
        for _ in $(seq 1 90); do
          if curl -fsS http://127.0.0.1:8081/v1/models >/dev/null 2>&1; then up=1; break; fi
          systemctl --user is-active --quiet mikronous-llama.service || break
          echo -n "."; sleep 2
        done
        echo
        if [[ "$up" == 1 ]]; then echo "llama-server is up"
        else fail "llama-server did not come up; see: journalctl --user -u mikronous-llama -n 50"; fi
      else
        fail "could not start mikronous-llama.service"
      fi
    else
      fail "model download failed (re-run, or: scripts/fetch-model.sh owner/repo file.gguf)"
    fi
  else
    fail "llama-server install failed (try MIKRONOUS_LLAMA_BACKEND=vulkan or cpu, or --no-model with your own server on :8081)"
  fi
fi

# ---------------------------------------------------------------------------
# Keep the profile's vision flag in step with the loaded model (install.sh just rewrote config.yaml).
MIK_BIN="$(command -v mik 2>/dev/null || echo "$HOME/.local/bin/mik")"
[[ -x "$MIK_BIN" ]] && "$MIK_BIN" model sync-config --no-restart >/dev/null 2>&1 || true

# systemd units are refreshed on EVERY run (the model step above is skipped by `mik update`), and a running
# server is restarted only when its unit actually changed, so a new flag such as --mmproj takes effect.
if command -v systemctl >/dev/null 2>&1; then
  mkdir -p "$UNIT_DIR"
  for unit in mikronous-llama.service mikronous-embed.service; do
    [[ "$unit" == mikronous-embed.service && ! -f "$UNIT_DIR/$unit" ]] && continue   # only once `mik embed on` installed it
    if ! cmp -s "$REPO_DIR/systemd/$unit" "$UNIT_DIR/$unit" 2>/dev/null; then
      cp "$REPO_DIR/systemd/$unit" "$UNIT_DIR/$unit"
      systemctl --user daemon-reload
      if systemctl --user is-active --quiet "$unit"; then
        systemctl --user restart "$unit" && echo "unit updated and restarted: $unit" || fail "could not restart $unit"
      else
        echo "unit updated: $unit"
      fi
    fi
  done
fi

step "Hermes gateway (API server + cron)"
# Hermes >= 0.21 runs ONE host gateway (default profile) that serves every profile; the mikronous
# API server is then mirrored at http://127.0.0.1:<default port>/p/mikronous/v1 and authenticated
# with mikronous's own API_SERVER_KEY. Older Hermes runs one gateway per profile.
DEFAULT_ENV="$HERMES_HOME/.env"; DEFAULT_CFG="$HERMES_HOME/config.yaml"
gen_key() { openssl rand -hex 24 2>/dev/null || head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
ensure_env_key() { # file key default
  grep -q "^$2=" "$1" 2>/dev/null || { echo "$2=$3" >> "$1"; echo "set $2 in $1"; }
}
is_multiplexed() {
  grep -qiE '^\s*standalone:\s*true' "$PROFILE_HOME/config.yaml" 2>/dev/null && return 1
  grep -qiE '^\s*multiplex_profiles:\s*true' "$DEFAULT_CFG" 2>/dev/null && return 0
  [[ -f "$UNIT_DIR/hermes-gateway.service" ]]
}
ensure_own_port() {
  # A standalone profile's listener must not collide with the default profile's API server (8642).
  HOST_PORT="$(grep ^API_SERVER_PORT= "$DEFAULT_ENV" 2>/dev/null | cut -d= -f2)"; HOST_PORT="${HOST_PORT:-8642}"
  API_PORT="$(grep ^API_SERVER_PORT= "$PROFILE_HOME/.env" | cut -d= -f2 | cut -d' ' -f1)"
  if [[ -z "$API_PORT" || "$API_PORT" == "$HOST_PORT" ]]; then
    API_PORT=$(( HOST_PORT + 1 ))
    if grep -q '^API_SERVER_PORT=' "$PROFILE_HOME/.env"; then sed -i "s|^API_SERVER_PORT=.*|API_SERVER_PORT=$API_PORT|" "$PROFILE_HOME/.env"
    else echo "API_SERVER_PORT=$API_PORT" >> "$PROFILE_HOME/.env"; fi
    echo "set API_SERVER_PORT=$API_PORT in $PROFILE_HOME/.env (host gateway owns $HOST_PORT)"
  fi
}
GATEWAY_MODE=""
if is_multiplexed; then
  GATEWAY_MODE=multiplex
else
  # Profile runs its own gateway (gateway.standalone: true in its config, or an older Hermes).
  ensure_own_port   # before the unit starts, so it never binds the host's port
  [[ -f "$UNIT_DIR/hermes-gateway-$PROFILE.service" ]] && already=1 || already=0
  printf 'y\ny\ny\n' | hp gateway install; rc=$?
  if [[ $rc -eq 0 ]]; then GATEWAY_MODE=standalone
  elif [[ $rc -eq 78 && "$already" == 1 ]]; then GATEWAY_MODE=standalone
  elif [[ $rc -eq 78 ]]; then GATEWAY_MODE=multiplex; echo "(per-profile gateways are retired; using the shared host gateway)"
  else fail "gateway install failed (exit $rc); run: hermes -p $PROFILE gateway install"; fi
fi
case "$GATEWAY_MODE" in
  multiplex)
    touch "$DEFAULT_ENV"; chmod 600 "$DEFAULT_ENV"
    ensure_env_key "$DEFAULT_ENV" API_SERVER_ENABLED true
    ensure_env_key "$DEFAULT_ENV" API_SERVER_KEY "$(gen_key)"
    ensure_env_key "$DEFAULT_ENV" API_SERVER_HOST 127.0.0.1
    ensure_env_key "$DEFAULT_ENV" API_SERVER_PORT 8642
    if [[ ! -f "$UNIT_DIR/hermes-gateway.service" ]]; then
      printf 'y\ny\ny\n' | hermes gateway install || fail "host gateway install failed; run: hermes gateway install"
    fi
    printf 'y\ny\n' | hermes gateway restart || printf 'y\ny\n' | hermes gateway start || fail "host gateway did not start; see: hermes gateway status"
    hp gateway restart >/dev/null 2>&1 || true   # make sure the host (re)serves this profile
    API_PORT="$(grep ^API_SERVER_PORT= "$DEFAULT_ENV" | cut -d= -f2)"
    API_URL="http://127.0.0.1:${API_PORT:-8642}/p/$PROFILE/v1"
    ;;
  standalone)
    # If a host gateway is running it must stop serving this profile; a restart makes it re-read the flag.
    if [[ -f "$UNIT_DIR/hermes-gateway.service" ]] && systemctl --user is-active --quiet hermes-gateway.service; then
      printf 'y\ny\n' | hermes gateway restart >/dev/null 2>&1 || true
    fi
    printf 'y\ny\n' | hp gateway restart || printf 'y\ny\n' | hp gateway start || fail "gateway did not start; see: hermes -p $PROFILE gateway status"
    API_URL="http://127.0.0.1:${API_PORT}/v1"
    ;;
  *) API_URL="(gateway not installed)" ;;
esac

# ---------------------------------------------------------------------------
if [[ "$WITH_TRAY" == 1 ]]; then
  step "Tray app (Meta+Space chat window)"
  if command -v mik >/dev/null 2>&1 || [[ -x "$HOME/.local/bin/mik" ]]; then
    MIK="$(command -v mik || echo "$HOME/.local/bin/mik")"
    if "$MIK" tray --render-icon "$CONF_DIR/.probe.png" >/dev/null 2>&1; then   # really imports PySide6
      rm -f "$CONF_DIR/.probe.png"
      mkdir -p "$(dirname "$TRAY_MARKER")"; touch "$TRAY_MARKER"
      APPS="$HOME/.local/share/applications"; ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
      mkdir -p "$APPS" "$ICONS" "$HOME/.config/autostart"
      sed "s|^Exec=mik |Exec=$MIK |" "$REPO_DIR/packaging/mikronous.desktop" > "$APPS/mikronous.desktop"
      sed "s|^Exec=mik |Exec=$MIK |" "$REPO_DIR/packaging/mikronous-tray-autostart.desktop" > "$HOME/.config/autostart/mikronous-tray.desktop"
      cp "$REPO_DIR/packaging/mikronous.svg" "$ICONS/mikronous.svg"
      cp "$REPO_DIR/packaging/mikronous-symbolic-light.svg" "$ICONS/mikronous-symbolic.svg"
      # 48px PNG for lookups that skip scalable/, and a touch so KDE's icon cache rescans the theme.
      PNGS="$HOME/.local/share/icons/hicolor/48x48/apps"; mkdir -p "$PNGS"
      "$MIK" tray --render-icon "$PNGS/mikronous.png" >/dev/null 2>&1 || true
      touch "$HOME/.local/share/icons/hicolor" "$HOME/.local/share/icons" 2>/dev/null || true
      command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true
      command -v kbuildsycoca5 >/dev/null 2>&1 && kbuildsycoca5 >/dev/null 2>&1 || true
      echo "installed: $APPS/mikronous.desktop, autostart entry, icon"
      # KRunner: `mik <question>` in Alt+Space (the tray serves org.kde.krunner1 over D-Bus).
      "$MIK" tray --install-krunner >/dev/null 2>&1 && {
        { command -v kquitapp6 >/dev/null 2>&1 && kquitapp6 krunner >/dev/null 2>&1; } \
          || { command -v kquitapp5 >/dev/null 2>&1 && kquitapp5 krunner >/dev/null 2>&1; } || true
        echo "installed: KRunner plugin (type: mik <question> in Alt+Space)"; } || true
      # Plasma only activates a .desktop file's X-KDE-Shortcuts once it is in kglobalshortcutsrc.
      # Plasma 6 (KF6): nested group [services][mikronous.desktop], _launch=<keys>.
      # Plasma 5 (KF5): flat group [mikronous.desktop], _launch=<keys>,none,<name> plus _k_friendly_name.
      HOTKEY="${MIKRONOUS_HOTKEY:-Meta+Space}"
      HOTKEY_SEL="${MIKRONOUS_HOTKEY_SELECTION:-Meta+Shift+Space}"
      HOTKEY_VOX="${MIKRONOUS_HOTKEY_VOX:-Meta+Shift+V}"
      HOTKEY_SCREEN="${MIKRONOUS_HOTKEY_SCREEN:-Meta+Shift+S}"
      if command -v kwriteconfig6 >/dev/null 2>&1; then KW=kwriteconfig6; KF=6
      elif command -v kwriteconfig5 >/dev/null 2>&1; then KW=kwriteconfig5; KF=5; else KW=""; fi
      write_hotkey() {
        if [[ "$KF" == 6 ]]; then
          "$KW" --file kglobalshortcutsrc --group services --group mikronous.desktop --key _launch "$HOTKEY"
          "$KW" --file kglobalshortcutsrc --group services --group mikronous.desktop --key selection "$HOTKEY_SEL"
          "$KW" --file kglobalshortcutsrc --group services --group mikronous.desktop --key vox "$HOTKEY_VOX"
          "$KW" --file kglobalshortcutsrc --group services --group mikronous.desktop --key screen "$HOTKEY_SCREEN"
        else
          "$KW" --file kglobalshortcutsrc --group services --group mikronous.desktop --key _launch --delete 2>/dev/null || true
          "$KW" --file kglobalshortcutsrc --group services --group mikronous.desktop --key selection --delete 2>/dev/null || true
          "$KW" --file kglobalshortcutsrc --group mikronous.desktop --key _k_friendly_name "Mikronous"
          "$KW" --file kglobalshortcutsrc --group mikronous.desktop --key _launch "$HOTKEY,none,Mikronous"
          "$KW" --file kglobalshortcutsrc --group mikronous.desktop --key selection "$HOTKEY_SEL,none,Mikronous: act on selected text"
          "$KW" --file kglobalshortcutsrc --group services --group mikronous.desktop --key vox --delete 2>/dev/null || true
          "$KW" --file kglobalshortcutsrc --group mikronous.desktop --key vox "$HOTKEY_VOX,none,Mikronous: voice input"
          "$KW" --file kglobalshortcutsrc --group services --group mikronous.desktop --key screen --delete 2>/dev/null || true
          "$KW" --file kglobalshortcutsrc --group mikronous.desktop --key screen "$HOTKEY_SCREEN,none,Mikronous: ask about the screen"
        fi
      }
      if [[ -n "$KW" ]]; then
        SECTION="$(sed -n '/\[mikronous.desktop\]/,/^\[/p' "$HOME/.config/kglobalshortcutsrc" 2>/dev/null)"
        # Keys are regex-escaped: a bare "Meta+Space" would read "Met", one or more "a", "Space" and never match,
        # so every run used to restart kglobalaccel and overwrite the user's own key choices.
        if ! grep -qE "^_launch=${HOTKEY//+/\\+}(,|$)" <<<"$SECTION" || ! grep -qE "^selection=${HOTKEY_SEL//+/\\+}(,|$)" <<<"$SECTION" \
           || ! grep -qE "^vox=${HOTKEY_VOX//+/\\+}(,|$)" <<<"$SECTION" || ! grep -qE "^screen=${HOTKEY_SCREEN//+/\\+}(,|$)" <<<"$SECTION"; then
          # The daemon writes its in-memory table to the file when it stops, so stop it BEFORE writing.
          if systemctl --user is-active --quiet plasma-kglobalaccel.service 2>/dev/null; then
            systemctl --user stop plasma-kglobalaccel.service; sleep 1
            write_hotkey
            systemctl --user start plasma-kglobalaccel.service
          else
            { command -v kquitapp6 >/dev/null 2>&1 && kquitapp6 kglobalaccel >/dev/null 2>&1; } \
              || { command -v kquitapp5 >/dev/null 2>&1 && kquitapp5 kglobalaccel5 >/dev/null 2>&1; } || true
            sleep 1
            write_hotkey
            if command -v kglobalaccel6 >/dev/null 2>&1; then (setsid kglobalaccel6 >/dev/null 2>&1 &)
            elif command -v kglobalaccel5 >/dev/null 2>&1; then (setsid kglobalaccel5 >/dev/null 2>&1 &); fi
          fi
        fi
        echo "shortcut: $HOTKEY opens the chat window; $HOTKEY_SEL acts on selected text; $HOTKEY_VOX voice input; $HOTKEY_SCREEN ask about the screen (System Settings > Shortcuts > Mikronous, or MIKRONOUS_HOTKEY= / _SELECTION= / _VOX= / _SCREEN=)"
      else
        echo "note: kwriteconfig not found; assign the shortcut in System Settings > Shortcuts > Add > Mikronous"
      fi
      if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
        "$MIK" show >/dev/null 2>&1 && echo "tray started (look for the blue M in the system tray)" \
          || fail "tray did not start; run: mik tray   (in a terminal) to see the error"
      else
        echo "no display in this shell; start it later with: mik tray"
      fi
    else
      fail "PySide6 missing in the mik environment; re-run without --no-tray or: pip install 'PySide6>=6.7' into it"
    fi
  else
    fail "mik is not installed, skipping tray"
  fi
fi

# ---------------------------------------------------------------------------
step "Done"
if (( ${#FAILURES[@]} )); then
  echo "Finished with ${#FAILURES[@]} problem(s):"
  for f in "${FAILURES[@]}"; do echo "  - $f"; done
else
  echo "Everything installed."
fi
echo
echo "Check everything:   mik doctor   (or: python3 -m mikronous_cli doctor)"
echo "Talk to it now:     hermes -p $PROFILE chat   (or just: mikronous chat)"
[[ "$WITH_TRAY" == 1 ]] && echo "Desktop:            ${MIKRONOUS_HOTKEY:-Meta+Space} (Meta = the Windows key) toggles the chat window; so does mik toggle."
echo "API server:         $API_URL  (Bearer key: API_SERVER_KEY in $PROFILE_HOME/.env)"
(( ${#FAILURES[@]} == 0 ))
