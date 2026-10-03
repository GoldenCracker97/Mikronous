#!/usr/bin/env bash
# Provide a `llama-server` binary for Mikronous and print its path.
#
# Order of preference:
#   1. $LLAMA_SERVER or a llama-server already on PATH
#   2. Build llama.cpp with CUDA when nvcc is present (best speed on NVIDIA; Linux has no
#      prebuilt CUDA archive upstream)
#   3. Download the latest prebuilt Vulkan build (works on NVIDIA/AMD/Intel, slower than CUDA)
#   4. Download the latest prebuilt CPU build
set -euo pipefail

PREFIX="${MIKRONOUS_LLAMA_PREFIX:-$HOME/.local/share/mikronous/llama.cpp}"
BACKEND="${MIKRONOUS_LLAMA_BACKEND:-auto}"   # auto | cuda | vulkan | cpu
BIN="$PREFIX/bin/llama-server"

log() { echo "[install-llama] $*" >&2; }

if [[ -n "${LLAMA_SERVER:-}" && -x "${LLAMA_SERVER}" ]]; then
  log "using LLAMA_SERVER=$LLAMA_SERVER"; echo "$LLAMA_SERVER"; exit 0
fi
if [[ -x "$BIN" && "${MIKRONOUS_LLAMA_REINSTALL:-0}" != "1" ]]; then
  log "already installed: $BIN"; echo "$BIN"; exit 0
fi
if found="$(command -v llama-server 2>/dev/null)"; then
  log "using llama-server from PATH: $found"; echo "$found"; exit 0
fi

mkdir -p "$PREFIX"

have_cuda() { command -v nvcc >/dev/null 2>&1 || [[ -x /usr/local/cuda/bin/nvcc ]]; }
have_nvidia() { command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; }

if [[ "$BACKEND" == "auto" ]]; then
  if have_nvidia && have_cuda; then BACKEND=cuda
  elif command -v vulkaninfo >/dev/null 2>&1 || [[ -e /dev/dri ]]; then BACKEND=vulkan
  else BACKEND=cpu; fi
  log "auto-selected backend: $BACKEND"
fi

build_cuda() {
  for t in git cmake make gcc; do
    command -v "$t" >/dev/null 2>&1 || { log "missing $t (needed to build with CUDA)"; return 1; }
  done
  export PATH="/usr/local/cuda/bin:$PATH"
  local src="$PREFIX/src"
  if [[ -d "$src/.git" ]]; then git -C "$src" pull --ff-only; else git clone --depth 1 https://github.com/ggml-org/llama.cpp "$src"; fi
  cmake -S "$src" -B "$src/build" -DGGML_CUDA=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DCMAKE_BUILD_TYPE=Release
  cmake --build "$src/build" --config Release --target llama-server -j"$(nproc)"
  mkdir -p "$PREFIX/bin"
  cp "$src/build/bin/llama-server" "$BIN"
  # Shared ggml libs live next to the binary in the build tree.
  cp "$src/build/bin/"*.so* "$PREFIX/bin/" 2>/dev/null || true
}

download_prebuilt() {
  local flavor="$1"   # vulkan | "" (cpu)
  command -v curl >/dev/null 2>&1 || { log "curl required"; return 1; }
  command -v unzip >/dev/null 2>&1 || { log "unzip required"; return 1; }
  local api="https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
  local pattern="ubuntu-${flavor:+$flavor-}x64.zip"
  local url
  url="$(curl -fsSL "$api" | grep -o '"browser_download_url": *"[^"]*'"$pattern"'"' | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')"
  [[ -n "$url" ]] || { log "no prebuilt asset matching $pattern"; return 1; }
  log "downloading $url"
  curl -fL --retry 3 -o "$PREFIX/llama.zip" "$url"
  rm -rf "$PREFIX/bin"; mkdir -p "$PREFIX/bin"
  unzip -oq "$PREFIX/llama.zip" -d "$PREFIX/unzip"
  # Archives contain either build/bin/* or a flat layout; take whatever holds llama-server.
  local srcbin
  srcbin="$(find "$PREFIX/unzip" -type f -name llama-server | head -1)"
  [[ -n "$srcbin" ]] || { log "llama-server not found in archive"; return 1; }
  cp "$(dirname "$srcbin")"/* "$PREFIX/bin/" 2>/dev/null || cp "$srcbin" "$BIN"
  chmod +x "$BIN"
  rm -rf "$PREFIX/unzip" "$PREFIX/llama.zip"
}

case "$BACKEND" in
  cuda)   build_cuda || { log "CUDA build failed; falling back to Vulkan prebuilt"; download_prebuilt vulkan; } ;;
  vulkan) download_prebuilt vulkan ;;
  cpu)    download_prebuilt "" ;;
  *)      log "unknown backend $BACKEND"; exit 1 ;;
esac

[[ -x "$BIN" ]] || { log "install failed"; exit 1; }
log "installed: $BIN ($BACKEND)"
echo "$BIN"
