#!/usr/bin/env bash
# Mikronous installer (Phase 0): Hermes profile + plugin + local llama-server + gateway service.
# Idempotent: re-running updates config in place and never overwrites secrets or memories.
#
#   scripts/install.sh            # everything
#   scripts/install.sh --no-model # skip model download / llama-server (already have one on :8081)
#
# Afterwards: `mik doctor` shows what is running.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="mikronous"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PROFILE_HOME="$HERMES_HOME/profiles/$PROFILE"
CONF_DIR="$HOME/.config/mikronous"
UNIT_DIR="$HOME/.config/systemd/user"
WITH_MODEL=1
for arg in "$@"; do
  case "$arg" in
    --no-model) WITH_MODEL=0 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }; }

need hermes
need systemctl

# ---------------------------------------------------------------------------
step "Hermes profile '$PROFILE'"
if hermes profile list 2>/dev/null | grep -qw "$PROFILE"; then
  echo "profile exists"
else
  hermes profile create "$PROFILE"
fi
[[ -d "$PROFILE_HOME" ]] || { echo "expected $PROFILE_HOME after profile create" >&2; exit 1; }

# SOUL.md: install once; the user (and the agent) own it afterwards.
if [[ -f "$PROFILE_HOME/SOUL.md" ]] && ! cmp -s "$REPO_DIR/profile/SOUL.md" "$PROFILE_HOME/SOUL.md" \
   && [[ "${MIKRONOUS_FORCE_SOUL:-0}" != "1" ]]; then
  echo "SOUL.md already customised; keeping it (MIKRONOUS_FORCE_SOUL=1 to overwrite)"
else
  cp "$REPO_DIR/profile/SOUL.md" "$PROFILE_HOME/SOUL.md"; echo "SOUL.md installed"
fi

# config.yaml: ours is the source of truth; keep a backup of anything that differs.
if [[ -f "$PROFILE_HOME/config.yaml" ]] && ! cmp -s "$REPO_DIR/profile/config.yaml" "$PROFILE_HOME/config.yaml"; then
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
  value="${line#*=}"; value="${value%% #*}"
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
# config.yaml already lists plugins.enabled: [mikronous]; this records the grant too.
"$PROFILE" plugins enable mikronous >/dev/null 2>&1 || hermes -p "$PROFILE" plugins enable mikronous || true

# ---------------------------------------------------------------------------
if [[ "$WITH_MODEL" == 1 ]]; then
  step "Local model + llama-server"
  LLAMA_BIN="$(bash "$REPO_DIR/scripts/install-llama.sh")"
  bash "$REPO_DIR/scripts/fetch-model.sh"
  MODEL_PATH="$HERMES_HOME/models/${MIKRONOUS_MODEL_FILE:-Qwen3-4B-Instruct-2507-Q4_K_M.gguf}"

  mkdir -p "$CONF_DIR" "$UNIT_DIR"
  if [[ ! -f "$CONF_DIR/llama.env" ]]; then
    sed -e "s|^LLAMA_SERVER=.*|LLAMA_SERVER=$LLAMA_BIN|" \
        -e "s|^LLAMA_MODEL=.*|LLAMA_MODEL=$MODEL_PATH|" \
        -e "s|^LLAMA_THREADS=.*|LLAMA_THREADS=$(( $(nproc) / 2 > 0 ? $(nproc) / 2 : 1 ))|" \
        "$REPO_DIR/systemd/mikronous-llama.env.example" > "$CONF_DIR/llama.env"
    echo "wrote $CONF_DIR/llama.env"
  else
    echo "keeping existing $CONF_DIR/llama.env (edit it or run \`mik model\`)"
  fi
  cp "$REPO_DIR/systemd/mikronous-llama.service" "$UNIT_DIR/"
  systemctl --user daemon-reload
  systemctl --user enable --now mikronous-llama.service
  echo "waiting for llama-server on :8081 ..."
  for _ in $(seq 1 60); do
    curl -fsS http://127.0.0.1:8081/v1/models >/dev/null 2>&1 && break
    sleep 2
  done
  curl -fsS http://127.0.0.1:8081/v1/models >/dev/null 2>&1 && echo "llama-server is up" \
    || echo "llama-server not up yet — check: journalctl --user -u mikronous-llama -f"
fi

# ---------------------------------------------------------------------------
step "Hermes gateway (API server + cron) as a user service"
"$PROFILE" gateway install 2>/dev/null || hermes -p "$PROFILE" gateway install
"$PROFILE" gateway restart 2>/dev/null || hermes -p "$PROFILE" gateway restart || true

# ---------------------------------------------------------------------------
step "mik CLI"
if command -v uv >/dev/null 2>&1; then
  (cd "$REPO_DIR" && uv tool install --force --editable . >/dev/null) && echo "installed: mik (uv tool)"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force --editable "$REPO_DIR" >/dev/null && echo "installed: mik (pipx)"
else
  echo "uv/pipx not found; run with: python3 -m mikronous_cli doctor  (from $REPO_DIR)"
fi

step "Done"
echo "Check everything:   mik doctor   (or: python3 -m mikronous_cli doctor)"
echo "Talk to it now:     $PROFILE chat"
echo "API server:         http://127.0.0.1:\$(grep ^API_SERVER_PORT= $PROFILE_HOME/.env | cut -d= -f2)/v1  (key in $PROFILE_HOME/.env)"
