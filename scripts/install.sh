#!/usr/bin/env bash
# Mikronous installer (Phase 0): Hermes profile + plugin + mik CLI + local llama-server + gateway service.
# Idempotent: re-running updates config in place and never overwrites secrets, memories, or an edited SOUL.md.
#
#   scripts/install.sh            # everything
#   scripts/install.sh --no-model # skip model download / llama-server (already have one on :8081)
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
FAILURES=()
for arg in "$@"; do
  case "$arg" in
    --no-model) WITH_MODEL=0 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

CURRENT_STEP="start"
step() { CURRENT_STEP="$*"; printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { echo "   !! $*" >&2; FAILURES+=("$CURRENT_STEP: $*"); }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }; }
hp() { hermes -p "$PROFILE" "$@"; }   # profile-scoped hermes; works before ~/.local/bin is on PATH
sha() { sha256sum "$1" | cut -d' ' -f1; }

need hermes
need systemctl

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
elif cmp -s "$REPO_DIR/profile/SOUL.md" "$SOUL_DST"; then reason="up to date"
elif head -c 60 "$SOUL_DST" | grep -q "^You are Hermes Agent"; then install_soul=1; reason="replacing Hermes starter"
elif [[ -f "$SOUL_SHA_FILE" && "$(cat "$SOUL_SHA_FILE")" == "$(sha "$SOUL_DST")" ]]; then install_soul=1; reason="updating unmodified Mikronous version"
elif [[ "${MIKRONOUS_FORCE_SOUL:-0}" == 1 ]]; then install_soul=1; reason="forced"
else reason="edited locally; keeping it (MIKRONOUS_FORCE_SOUL=1 to overwrite)"
fi
if [[ "$install_soul" == 1 ]]; then
  cp "$REPO_DIR/profile/SOUL.md" "$SOUL_DST"
fi
[[ -f "$SOUL_DST" ]] && cmp -s "$REPO_DIR/profile/SOUL.md" "$SOUL_DST" && sha "$SOUL_DST" > "$SOUL_SHA_FILE"
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
  "$venv/bin/pip" install --quiet --editable "$REPO_DIR" >/dev/null 2>&1 || return 1
  mkdir -p "$HOME/.local/bin" && ln -sfn "$venv/bin/mik" "$HOME/.local/bin/mik"
}
if command -v uv >/dev/null 2>&1; then
  (cd "$REPO_DIR" && uv tool install --force --editable . >/dev/null 2>&1) && echo "installed: mik (uv tool)" || fail "uv tool install failed"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force --editable "$REPO_DIR" >/dev/null 2>&1 && echo "installed: mik (pipx)" || fail "pipx install failed"
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
step "Hermes gateway (API server + cron) as a user service"
# stdin from /dev/null makes Hermes take its defaults (start now + start on login) without prompting.
if hp gateway install </dev/null; then
  hp gateway restart </dev/null || hp gateway start </dev/null || fail "gateway did not start; see: hermes -p $PROFILE gateway status"
else
  fail "gateway install failed; run: hermes -p $PROFILE gateway install"
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
echo "API server:         http://127.0.0.1:$(grep ^API_SERVER_PORT= "$PROFILE_HOME/.env" | cut -d= -f2)/v1  (key in $PROFILE_HOME/.env)"
(( ${#FAILURES[@]} == 0 ))
