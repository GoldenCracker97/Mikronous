"""Minimal GGUF metadata reader (no dependencies) for local files and Hugging Face URLs.

Only the metadata key/value block is parsed — enough for layer count, KV heads, head size,
training context and architecture. Weight size comes from the file size (within ~1% of the
tensor data). Remote files are read with HTTP range requests, a few MiB at most.
"""

from __future__ import annotations

import struct
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

GGUF_MAGIC = b"GGUF"
_CHUNK = 4 * 1024 * 1024

# value type -> struct format (scalars)
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_T_STRING, _T_ARRAY = 8, 9


class _Source:
    """Random-access bytes over a local file or an HTTP URL (range requests, cached chunks)."""

    def __init__(self, target: str):
        self.target = target
        self.remote = target.startswith(("http://", "https://"))
        self._cache: dict[int, bytes] = {}
        self.size = self._probe_size()

    def _probe_size(self) -> int:
        if not self.remote:
            return Path(self.target).stat().st_size
        req = urllib.request.Request(self.target, method="HEAD", headers={"User-Agent": "mikronous"})
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            size = resp.headers.get("x-linked-size") or resp.headers.get("Content-Length") or "0"
            return int(size)

    def _chunk(self, idx: int) -> bytes:
        if idx in self._cache:
            return self._cache[idx]
        start = idx * _CHUNK
        if self.remote:
            req = urllib.request.Request(self.target, headers={"Range": f"bytes={start}-{start + _CHUNK - 1}",
                                                               "User-Agent": "mikronous"})
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                data = resp.read()
        else:
            with open(self.target, "rb") as fh:
                fh.seek(start)
                data = fh.read(_CHUNK)
        self._cache[idx] = data
        if len(self._cache) > 8:  # keep memory bounded; metadata is read sequentially
            self._cache.pop(next(iter(self._cache)))
        return data

    def read(self, offset: int, length: int) -> bytes:
        out = bytearray()
        while length > 0:
            idx, within = divmod(offset, _CHUNK)
            piece = self._chunk(idx)[within:within + length]
            if not piece:
                raise EOFError("unexpected end of GGUF data")
            out += piece
            offset += len(piece)
            length -= len(piece)
        return bytes(out)


@dataclass
class GGUFMeta:
    path: str
    file_bytes: int
    arch: str = ""
    name: str = ""
    block_count: int = 0
    head_count: int = 0
    head_count_kv: int = 0
    embedding_length: int = 0
    key_length: int = 0
    value_length: int = 0
    context_length: int = 0
    expert_count: int = 0
    expert_used_count: int = 0
    file_type: int = -1
    vocab_size: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def head_dim(self) -> int:
        if self.key_length:
            return self.key_length
        if self.head_count and self.embedding_length:
            return self.embedding_length // self.head_count
        return 128

    @property
    def is_moe(self) -> bool:
        return self.expert_count > 1

    def kv_bytes_per_token(self, k_bytes: float, v_bytes: float) -> float:
        """Bytes of KV cache per context token: K + V for every layer and KV head."""
        kv_heads = self.head_count_kv or self.head_count or 8
        k_dim = self.key_length or self.head_dim
        v_dim = self.value_length or self.head_dim
        return self.block_count * kv_heads * (k_dim * k_bytes + v_dim * v_bytes)


class _Reader:
    def __init__(self, src: _Source):
        self.src = src
        self.pos = 0

    def take(self, n: int) -> bytes:
        data = self.src.read(self.pos, n)
        self.pos += n
        return data

    def scalar(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(size))[0]

    def string(self, keep: bool = True) -> str:
        n = self.scalar("<Q")
        if n > 64 * 1024 * 1024:
            raise ValueError("implausible GGUF string length")
        data = self.take(n) if keep or n < 4096 else self.skip_bytes(n)
        return data.decode("utf-8", "replace") if keep else ""

    def skip_bytes(self, n: int) -> bytes:
        self.pos += n
        return b""

    def value(self, vtype: int, keep: bool):
        if vtype in _SCALARS:
            return self.scalar(_SCALARS[vtype])
        if vtype == _T_STRING:
            return self.string(keep)
        if vtype == _T_ARRAY:
            etype = self.scalar("<I")
            n = self.scalar("<Q")
            if etype in _SCALARS and not keep:
                self.skip_bytes(struct.calcsize(_SCALARS[etype]) * n)
                return None
            out = [] if keep else None
            for _ in range(n):
                v = self.value(etype, keep)
                if keep:
                    out.append(v)  # type: ignore[union-attr]
            return out
        raise ValueError(f"unknown GGUF value type {vtype}")


_WANT = {
    "block_count", "attention.head_count", "attention.head_count_kv", "embedding_length",
    "attention.key_length", "attention.value_length", "context_length", "expert_count", "expert_used_count",
}


def read_meta(target: str) -> GGUFMeta:
    """Parse metadata from a local .gguf path or an https URL (Hugging Face resolve link)."""
    src = _Source(target)
    r = _Reader(src)
    if r.take(4) != GGUF_MAGIC:
        raise ValueError(f"not a GGUF file: {target}")
    version = r.scalar("<I")
    if version < 2:
        raise ValueError(f"unsupported GGUF version {version}")
    _tensor_count = r.scalar("<Q")
    kv_count = r.scalar("<Q")
    meta = GGUFMeta(path=target, file_bytes=src.size)
    for _ in range(kv_count):
        key = r.string()
        vtype = r.scalar("<I")
        suffix = key.split(".", 1)[1] if "." in key and not key.startswith("general.") and not key.startswith("tokenizer.") else key
        keep = key.startswith("general.") or suffix in _WANT or key == "tokenizer.ggml.model"
        if key.startswith("tokenizer.") and key != "tokenizer.ggml.model":
            keep = False
        val = r.value(vtype, keep)
        if not keep:
            if key == "tokenizer.ggml.tokens":
                pass
            continue
        if key == "general.architecture":
            meta.arch = str(val)
        elif key == "general.name":
            meta.name = str(val)
        elif key == "general.file_type":
            meta.file_type = int(val)
        elif suffix == "block_count":
            meta.block_count = int(val)
        elif suffix == "attention.head_count":
            meta.head_count = int(val)
        elif suffix == "attention.head_count_kv":
            meta.head_count_kv = int(val) if not isinstance(val, list) else int(max(val))
        elif suffix == "embedding_length":
            meta.embedding_length = int(val)
        elif suffix == "attention.key_length":
            meta.key_length = int(val)
        elif suffix == "attention.value_length":
            meta.value_length = int(val)
        elif suffix == "context_length":
            meta.context_length = int(val)
        elif suffix == "expert_count":
            meta.expert_count = int(val)
        elif suffix == "expert_used_count":
            meta.expert_used_count = int(val)
        elif key.startswith("general."):
            meta.extra[key] = val
    return meta


def hf_resolve_url(repo: str, filename: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{filename}"


def hf_list_gguf(repo: str) -> list[tuple[str, int]]:
    """(filename, size) for every .gguf in a Hugging Face repo, via the public API."""
    import json
    req = urllib.request.Request(f"https://huggingface.co/api/models/{repo}?blobs=true",
                                 headers={"User-Agent": "mikronous"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Hugging Face repo not found or not public: {repo} (HTTP {exc.code})") from exc
    files = []
    for s in data.get("siblings", []):
        name = s.get("rfilename", "")
        if name.endswith(".gguf"):
            files.append((name, int(s.get("size") or 0)))
    return sorted(files)
