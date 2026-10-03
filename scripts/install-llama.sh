#!/usr/bin/env bash
# Provide a `llama-server` binary for Mikronous and print its path on stdout.
#
# Order of preference:
#   1. $LLAMA_SERVER (explicit) or an existing install under $PREFIX
#   2. Prebuilt nightly from github.com/ggml-org/llama.cpp (Linux x64 tarballs):
#        cuda-13.4 / cuda-12.8  NVIDIA, picked from the driver's reported CUDA version (no nvcc needed)
#        vulkan                  any GPU (NVIDIA/AMD/Intel)
#        cpu                     no GPU
#   3. `cuda-build`: compile from source (opt-in, needs nvcc + cmake)
#
# Env overrides:
#   MIKRONOUS_LLAMA_BACKEND=auto|cuda|cuda-13.4|cuda-12.8|vulkan|cpu|cuda-build
#   MIKRONOUS_LLAMA_TAG=b11146        pin a nightly tag (default: latest)
#   MIKRONOUS_LLAMA_PREFIX=...        install dir (default ~/.local/share/mikronous/llama.cpp)
#   MIKRONOUS_LLAMA_REINSTALL=1       replace an existing install
set -euo pipefail

PREFIX="${MIKRONOUS_LLAMA_PREFIX:-$HOME/.local/share/mikronous/llama.cpp}"
BACKEND="${MIKRONOUS_LLAMA_BACKEND:-auto}"
BIN="$PREFIX/bin/llama-server"
GH="https://github.com/ggml-org/llama.cpp/releases"

log() { echo "[install-llama] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }
trap 'log "failed at line $LINENO (backend=$BACKEND)"' ERR

if [[ -n "${LLAMA_SERVER:-}" ]]; then
  [[ -x "$LLAMA_SERVER" ]] || die "LLAMA_SERVER=$LLAMA_SERVER is not executable"
  log "using LLAMA_SERVER=$LLAMA_SERVER"; echo "$LLAMA_SERVER"; exit 0
fi
if [[ -x "$BIN" && "${MIKRONOUS_LLAMA_REINSTALL:-0}" != "1" ]]; then
  log "already installed: $BIN ($(cat "$PREFIX/BACKEND" 2>/dev/null || echo unknown))"; echo "$BIN"; exit 0
fi

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) ARCH_TAG="x64" ;;
  aarch64|arm64) ARCH_TAG="arm64" ;;
  *) die "unsupported architecture: $ARCH" ;;
esac

# --- hardware detection ------------------------------------------------------
nvidia_cuda_version() {
  # "CUDA Version: 12.8" from the nvidia-smi banner; empty when no NVIDIA driver.
  command -v nvidia-smi >/dev/null 2>&1 || return 0
  nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: *\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -1 || true
}
ver_ge() { # ver_ge 12.9 12.8 -> true
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" == "$2" ]]
}
has_gpu_node() { [[ -e /dev/dri ]] || command -v vulkaninfo >/dev/null 2>&1; }

if [[ "$BACKEND" == "auto" || "$BACKEND" == "cuda" ]]; then
  cuda_ver="$(nvidia_cuda_version)"
  if [[ -n "$cuda_ver" ]]; then
    if ver_ge "$cuda_ver" 13.4; then BACKEND="cuda-13.4"
    elif ver_ge "$cuda_ver" 12.8; then BACKEND="cuda-12.8"
    else
      log "NVIDIA driver reports CUDA $cuda_ver (< 12.8); prebuilt CUDA needs a newer driver — using Vulkan"
      BACKEND="vulkan"
    fi
    log "NVIDIA GPU detected (driver CUDA $cuda_ver) -> $BACKEND"
  elif [[ "$BACKEND" == "cuda" ]]; then
    die "backend=cuda requested but nvidia-smi found no NVIDIA driver"
  elif has_gpu_node; then
    BACKEND="vulkan"; log "non-NVIDIA GPU detected -> vulkan"
  else
    BACKEND="cpu"; log "no GPU detected -> cpu"
  fi
fi

# --- prebuilt download ---------------------------------------------------------
resolve_tag() {
  if [[ -n "${MIKRONOUS_LLAMA_TAG:-}" ]]; then echo "$MIKRONOUS_LLAMA_TAG"; return; fi
  local tag
  tag="$(curl -fsSL "$GH/latest/download/nightly-tag.txt" 2>/dev/null | tr -d '[:space:]' || true)"
  [[ -n "$tag" ]] || die "could not resolve the latest llama.cpp nightly tag (set MIKRONOUS_LLAMA_TAG=bNNNNN)"
  echo "$tag"
}

fetch_and_extract() { # fetch_and_extract <url> <destdir>
  local url="$1" dest="$2" tmp
  tmp="$(mktemp -d)"
  log "downloading $(basename "$url")"
  curl -fL --retry 3 --progress-bar -o "$tmp/pkg.tar.gz" "$url" || die "download failed: $url"
  tar -xzf "$tmp/pkg.tar.gz" -C "$tmp" || die "could not extract $(basename "$url")"
  rm -f "$tmp/pkg.tar.gz"
  # Archives wrap everything in one top-level dir (llama-bNNNN/) or ship files flat. Merge the
  # contents into $dest with cp -a so the libfoo.so -> libfoo.so.0 -> libfoo.so.0.x symlinks survive.
  local entry
  for entry in "$tmp"/* "$tmp"/.[!.]*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    if [[ -d "$entry" && ! -L "$entry" ]]; then cp -a "$entry"/. "$dest"/; else cp -a "$entry" "$dest"/; fi
  done
  rm -rf "$tmp"
}

download_prebuilt() {
  for t in curl tar; do command -v "$t" >/dev/null 2>&1 || die "missing required command: $t"; done
  local tag flavor; tag="$(resolve_tag)"
  case "$BACKEND" in
    cuda-13.4|cuda-12.8) flavor="${BACKEND}-${ARCH_TAG}" ;;
    vulkan)              flavor="vulkan-${ARCH_TAG}" ;;
    cpu)                 flavor="${ARCH_TAG}" ;;
    *) die "no prebuilt for backend $BACKEND" ;;
  esac
  local stage="$PREFIX/bin.new"
  rm -rf "$stage"; mkdir -p "$stage"
  fetch_and_extract "$GH/download/$tag/llama-$tag-bin-ubuntu-$flavor.tar.gz" "$stage"
  if [[ "$BACKEND" == cuda-* ]]; then
    # CUDA runtime libraries ship separately so users do not need the CUDA toolkit installed.
    fetch_and_extract "$GH/download/$tag/cudart-llama-$tag-bin-ubuntu-$flavor.tar.gz" "$stage"
  fi
  [[ -f "$stage/llama-server" ]] || die "llama-server not found in the $flavor archive"
  chmod +x "$stage"/llama-* 2>/dev/null || true
  rm -rf "$PREFIX/bin"; mv "$stage" "$PREFIX/bin"
  echo "$tag" > "$PREFIX/TAG"
}

# --- source build (opt-in) -----------------------------------------------------
build_cuda() {
  for t in git cmake make gcc nvcc; do
    command -v "$t" >/dev/null 2>&1 || [[ -x "/usr/local/cuda/bin/$t" ]] || die "missing $t (needed for cuda-build)"
  done
  export PATH="/usr/local/cuda/bin:$PATH"
  local src="$PREFIX/src"
  if [[ -d "$src/.git" ]]; then git -C "$src" pull --ff-only; else git clone --depth 1 https://github.com/ggml-org/llama.cpp "$src"; fi
  cmake -S "$src" -B "$src/build" -DGGML_CUDA=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DCMAKE_BUILD_TYPE=Release
  cmake --build "$src/build" --config Release --target llama-server -j"$(nproc)"
  rm -rf "$PREFIX/bin"; mkdir -p "$PREFIX/bin"
  cp "$src/build/bin/llama-server" "$BIN"
  cp "$src/build/bin/"*.so* "$PREFIX/bin/" 2>/dev/null || true
  git -C "$src" rev-parse --short HEAD > "$PREFIX/TAG"
}

mkdir -p "$PREFIX"
case "$BACKEND" in
  cuda-13.4|cuda-12.8|vulkan|cpu) download_prebuilt ;;
  cuda-build) build_cuda ;;
  *) die "unknown backend: $BACKEND" ;;
esac

[[ -x "$BIN" ]] || die "install finished but $BIN is missing"
echo "$BACKEND" > "$PREFIX/BACKEND"
# Shared libs sit next to the binary; make sure the loader finds them even without rpath.
if ! "$BIN" --version >/dev/null 2>&1 && ! LD_LIBRARY_PATH="$PREFIX/bin" "$BIN" --version >/dev/null 2>&1; then
  log "warning: $BIN does not start; check 'LD_LIBRARY_PATH=$PREFIX/bin $BIN --version'"
fi
log "installed: $BIN ($BACKEND, $(cat "$PREFIX/TAG"))"
echo "$BIN"
