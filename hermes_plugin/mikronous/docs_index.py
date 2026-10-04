"""Full-text index over the user's document folders (SQLite FTS5, BM25), plus optional semantic chunks.

With `mik embed on` a small CPU embedding server (llama.cpp, nomic-embed-text) runs on :8082; every
indexed file is also split into ~800-character chunks whose vectors live in the ``chunks`` table, and
``search`` fuses BM25 and cosine ranks (reciprocal rank fusion). Without it, keyword search only.

Text comes straight from text-like files; PDFs and Office documents go through Hermes's own
``tools.read_extract.extract_document_text`` when it is importable (it always is inside the
Hermes process; `mik docs reindex` adds the Hermes checkout to sys.path to get it too).
"""

from __future__ import annotations

import array
import json
import math
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ._paths import conf_dir as _conf_dir
from ._paths import data_dir as _data_dir
from ._paths import hermes_home as _hermes_home

DATA_DIR = _data_dir()
DB_PATH = DATA_DIR / "docs.sqlite"
TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".org", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini",
            ".cfg", ".log", ".tex", ".html", ".htm", ".xml", ".py", ".js", ".ts", ".sh", ".c", ".h", ".cpp", ".java",
            ".go", ".rs", ".rb", ".php", ".sql"}
DOC_EXT = {".pdf", ".docx", ".doc", ".odt", ".rtf", ".epub", ".pptx", ".ppt", ".odp", ".xlsx", ".xls", ".ods", ".ipynb"}
MAX_BYTES = 25 * 1024 * 1024
MAX_CHARS = 400_000
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".cache", "Trash", ".Trash"}
STALE_SECONDS = 600
CHUNK_CHARS = 800
CHUNK_OVERLAP = 120
MAX_CHUNKS_PER_FILE = 400
EMBED_BATCH = 16


def docs_dirs() -> list[Path]:
    raw = os.environ.get("MIKRONOUS_DOCS_DIRS") or _env_from_profile("MIKRONOUS_DOCS_DIRS") or "~/Documents"
    out = []
    for part in raw.split(os.pathsep):          # ':' on Linux, ';' on Windows (drive letters contain ':')
        p = Path(part.strip()).expanduser()
        if part.strip() and p.is_dir():
            out.append(p)
    return out


def _env_from_profile(key: str) -> str:
    env_file = _hermes_home()
    for candidate in (env_file / ".env", env_file / "profiles" / "mikronous" / ".env"):
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def _db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, indexed_at REAL, error TEXT)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(path UNINDEXED, title, body, tokenize='porter unicode61')")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS chunks (path TEXT, idx INTEGER, text TEXT, vec BLOB, PRIMARY KEY (path, idx))")
    return con


# ----------------------------------------------------------------------------- embeddings (optional)
def _embed_env() -> dict[str, str]:
    """embed.env written by `mik embed on` (same conf dir as llama.env); {} when semantic search is off."""
    conf = _conf_dir()
    out: dict[str, str] = {}
    try:
        for line in (conf / "embed.env").read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    except OSError:
        return {}
    return out


def embed_enabled() -> bool:
    return _embed_env().get("EMBED_ENABLED", "0") == "1"


def _embed_texts(texts: list[str], kind: str = "document") -> list[list[float]] | None:
    """Vectors from the local embedding server, or None when it is off/unreachable. Monkeypatched in tests."""
    e = _embed_env()
    if e.get("EMBED_ENABLED", "0") != "1" or not texts:
        return None
    prefix = e.get("EMBED_PREFIX_QUERY" if kind == "query" else "EMBED_PREFIX_DOC", "")
    body = json.dumps({"input": [prefix + t for t in texts]}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{e.get('EMBED_PORT', '8082')}/v1/embeddings", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - local server
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    rows = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
    vecs = [r.get("embedding") for r in rows]
    return vecs if len(vecs) == len(texts) and all(isinstance(v, list) for v in vecs) else None


def chunk_text(body: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Overlapping windows that prefer to break at a blank line or sentence end."""
    body = body.strip()
    if not body:
        return []
    out: list[str] = []
    i = 0
    while i < len(body) and len(out) < MAX_CHUNKS_PER_FILE:
        end = min(len(body), i + size)
        if end < len(body):
            cut = max(body.rfind("\n\n", i + size // 2, end), body.rfind(". ", i + size // 2, end))
            if cut > i:
                end = cut + 1
        out.append(body[i:end].strip())
        if end >= len(body):
            break
        i = max(end - overlap, i + 1)
    return [c for c in out if c]


def _pack(vec: list[float]) -> bytes:
    a = array.array("f", vec)
    norm = math.sqrt(sum(x * x for x in a)) or 1.0
    return array.array("f", [x / norm for x in a]).tobytes()


def _unpack(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    return a


def _embed_file(con: sqlite3.Connection, key: str, body: str) -> bool:
    chunks = chunk_text(body)
    con.execute("DELETE FROM chunks WHERE path = ?", (key,))
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start:start + EMBED_BATCH]
        vecs = _embed_texts(batch, "document")
        if vecs is None:
            return False
        con.executemany("INSERT OR REPLACE INTO chunks (path, idx, text, vec) VALUES (?, ?, ?, ?)",
                        [(key, start + j, batch[j], _pack(vecs[j])) for j in range(len(batch))])
    return True


def _semantic_hits(con: sqlite3.Connection, query: str, limit: int = 20) -> list[tuple[str, float, str]]:
    """[(path, cosine, chunk_text)] best chunk per file, highest first."""
    qv = _embed_texts([query], "query")
    if not qv:
        return []
    q = _unpack(_pack(qv[0]))
    best: dict[str, tuple[float, str]] = {}
    try:
        import numpy as np
        qn = np.frombuffer(q.tobytes(), dtype=np.float32)
        for path, text, blob in con.execute("SELECT path, text, vec FROM chunks"):
            score = float(np.dot(qn, np.frombuffer(blob, dtype=np.float32)))
            if score > best.get(path, (-2.0, ""))[0]:
                best[path] = (score, text)
    except ImportError:
        for path, text, blob in con.execute("SELECT path, text, vec FROM chunks"):
            v = _unpack(blob)
            score = sum(a * b for a, b in zip(q, v))
            if score > best.get(path, (-2.0, ""))[0]:
                best[path] = (score, text)
    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    return [(p, s, t) for p, (s, t) in ranked]


def rrf(*rankings: list[str], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion of ordered path lists."""
    out: dict[str, float] = {}
    for ranking in rankings:
        for rank, path in enumerate(ranking):
            out[path] = out.get(path, 0.0) + 1.0 / (k + rank + 1)
    return out


def _hermes_extract():
    """Hermes's document extractor, if importable (inside Hermes, or with the checkout on sys.path)."""
    try:
        from tools.read_extract import extract_document_text  # type: ignore
        return extract_document_text
    except Exception:  # noqa: BLE001
        pass
    checkout = Path(os.environ.get("HERMES_AGENT_DIR", "~/.hermes/hermes-agent")).expanduser()
    if checkout.is_dir() and str(checkout) not in sys.path:
        sys.path.append(str(checkout))
        try:
            from tools.read_extract import extract_document_text  # type: ignore
            return extract_document_text
        except Exception:  # noqa: BLE001
            return None
    return None


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in TEXT_EXT:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS]
    if ext in DOC_EXT:
        fn = _hermes_extract()
        if fn is None:
            raise RuntimeError("document extraction unavailable outside Hermes (pdf/docx need Hermes's extractor)")
        return str(fn(str(path)))[:MAX_CHARS]
    raise ValueError("unsupported type")


def _walk(dirs: list[Path]):
    for base in dirs:
        for root, dnames, fnames in os.walk(base):
            dnames[:] = [d for d in dnames if not d.startswith(".") and d not in SKIP_DIRS]
            for f in fnames:
                if f.startswith("."):
                    continue
                p = Path(root) / f
                if p.suffix.lower() in TEXT_EXT or p.suffix.lower() in DOC_EXT:
                    yield p


@dataclass
class IndexStats:
    scanned: int = 0
    indexed: int = 0
    removed: int = 0
    errors: int = 0
    seconds: float = 0.0
    embedded: int = 0
    embed_skipped: int = 0


def reindex(force: bool = False, dirs: list[Path] | None = None, embed_missing: bool = False) -> IndexStats:
    t0 = time.time()
    dirs = dirs if dirs is not None else docs_dirs()
    con = _db()
    known = {r[0]: (r[1], r[2]) for r in con.execute("SELECT path, mtime, size FROM files")}
    seen: set[str] = set()
    st = IndexStats()
    embed_on = embed_enabled()
    for p in _walk(dirs):
        key = str(p)
        seen.add(key)
        st.scanned += 1
        try:
            s = p.stat()
        except OSError:
            continue
        if s.st_size > MAX_BYTES:
            continue
        if not force and known.get(key) == (s.st_mtime, s.st_size):
            continue
        err = ""
        try:
            body = extract_text(p)
            con.execute("DELETE FROM docs_fts WHERE path = ?", (key,))
            con.execute("INSERT INTO docs_fts (path, title, body) VALUES (?, ?, ?)", (key, p.name, body))
            st.indexed += 1
            if embed_on:
                if _embed_file(con, key, body):
                    st.embedded += 1
                else:
                    st.embed_skipped += 1
                    embed_on = False          # server gone: stop trying for this pass, keep the keyword index
            else:
                con.execute("DELETE FROM chunks WHERE path = ?", (key,))
        except Exception as exc:  # noqa: BLE001 - one bad file never stops the pass
            err = f"{exc.__class__.__name__}: {exc}"[:200]
            st.errors += 1
        con.execute("INSERT OR REPLACE INTO files (path, mtime, size, indexed_at, error) VALUES (?, ?, ?, ?, ?)",
                    (key, s.st_mtime, s.st_size, time.time(), err))
        if st.indexed % 50 == 0:
            con.commit()
    for gone in set(known) - seen:
        con.execute("DELETE FROM docs_fts WHERE path = ?", (gone,))
        con.execute("DELETE FROM files WHERE path = ?", (gone,))
        con.execute("DELETE FROM chunks WHERE path = ?", (gone,))
        st.removed += 1
    if embed_on and embed_missing:            # files indexed before semantic search was switched on
        missing = [r[0] for r in con.execute(
            "SELECT f.path FROM files f WHERE f.error = '' AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.path = f.path)")]
        for key in missing:
            row = con.execute("SELECT body FROM docs_fts WHERE path = ?", (key,)).fetchone()
            if row is None:
                continue
            if _embed_file(con, key, row[0]):
                st.embedded += 1
            else:
                st.embed_skipped += 1
                break
            if st.embedded % 20 == 0:
                con.commit()
    con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_reindex', ?)", (str(time.time()),))
    con.commit()
    con.close()
    st.seconds = round(time.time() - t0, 2)
    return st


def last_reindex() -> float:
    try:
        con = _db()
        row = con.execute("SELECT value FROM meta WHERE key = 'last_reindex'").fetchone()
        con.close()
        return float(row[0]) if row else 0.0
    except (sqlite3.Error, ValueError):
        return 0.0


def ensure_fresh(max_age: float = STALE_SECONDS) -> None:
    if time.time() - last_reindex() > max_age:
        reindex()


def search(query: str, limit: int = 8) -> list[dict]:
    import re
    ensure_fresh()
    con = _db()
    toks = re.findall(r"\w+", query)
    if not toks:
        con.close()
        return []
    q = " ".join(f'"{t}"' for t in toks)
    sql = ("SELECT path, title, bm25(docs_fts, 2.0, 1.0) AS score, snippet(docs_fts, 2, '[', ']', ' … ', 24) "
           "FROM docs_fts WHERE docs_fts MATCH ? ORDER BY score LIMIT ?")
    try:
        rows = con.execute(sql, (q, max(limit, 50) if embed_enabled() else limit)).fetchall()
        if not rows and len(toks) > 1:  # fall back to ANY-term match
            rows = con.execute(sql, (" OR ".join(f'"{t}"' for t in toks), max(limit, 50) if embed_enabled() else limit)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    keyword = [{"path": r[0], "title": r[1], "score": round(-float(r[2]), 3), "snippet": r[3]} for r in rows]
    semantic = _semantic_hits(con, query) if embed_enabled() else []
    if not semantic:
        con.close()
        return keyword[:limit]
    fused = rrf([h["path"] for h in keyword], [p for p, _s, _t in semantic])
    by_path = {h["path"]: h for h in keyword}
    for path, score, text in semantic:
        by_path.setdefault(path, {"path": path, "title": Path(path).name, "score": round(score, 3),
                                  "snippet": text[:220].replace("\n", " ") + ("…" if len(text) > 220 else "")})
    out = sorted(by_path.values(), key=lambda h: fused.get(h["path"], 0.0), reverse=True)[:limit]
    for h in out:
        h["score"] = round(fused.get(h["path"], 0.0), 4)
        h["semantic"] = True
    con.close()
    return out


def stats() -> dict:
    con = _db()
    files = con.execute("SELECT COUNT(*), SUM(CASE WHEN error != '' THEN 1 ELSE 0 END) FROM files").fetchone()
    chunks = con.execute("SELECT COUNT(*), COUNT(DISTINCT path) FROM chunks").fetchone()
    con.close()
    return {"files": files[0] or 0, "errors": files[1] or 0, "last_reindex": last_reindex(),
            "dirs": [str(d) for d in docs_dirs()], "db": str(DB_PATH),
            "semantic": embed_enabled(), "chunks": chunks[0] or 0, "files_embedded": chunks[1] or 0}
