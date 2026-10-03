#!/usr/bin/env bash
# Download a GGUF into ~/.hermes/models (shared with Hermes's own local runtime).
#
# Usage:
#   scripts/fetch-model.sh                      # default model for ~8 GB VRAM
#   scripts/fetch-model.sh owner/repo file.gguf # any Hugging Face GGUF
#
# Default: Qwen3-4B-Instruct-2507 Q4_K_M (~2.5 GB) — tool calling, 262k native context,
# fits an 8 GB card at 64k context with quantised KV cache. `mik model` (Phase 0.5)
# picks better fits for other hardware.
set -euo pipefail

REPO="${1:-${MIKRONOUS_MODEL_REPO:-unsloth/Qwen3-4B-Instruct-2507-GGUF}}"
FILE="${2:-${MIKRONOUS_MODEL_FILE:-Qwen3-4B-Instruct-2507-Q4_K_M.gguf}}"
MODELS_DIR="${MIKRONOUS_MODELS_DIR:-${HERMES_HOME:-$HOME/.hermes}/models}"
DEST="$MODELS_DIR/$FILE"

mkdir -p "$MODELS_DIR"
if [[ -s "$DEST" ]]; then
  echo "already present: $DEST"
  exit 0
fi

echo "downloading $REPO/$FILE -> $DEST"
if command -v hf >/dev/null 2>&1; then
  hf download "$REPO" "$FILE" --local-dir "$MODELS_DIR"
elif command -v huggingface-cli >/dev/null 2>&1; then
  huggingface-cli download "$REPO" "$FILE" --local-dir "$MODELS_DIR"
else
  URL="https://huggingface.co/$REPO/resolve/main/$FILE"
  # -C - resumes an interrupted download.
  curl -L --fail --retry 3 -C - -o "$DEST.part" "$URL"
  mv "$DEST.part" "$DEST"
fi
echo "done: $DEST"
