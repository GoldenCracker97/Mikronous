"""Full-text index over the user's document folders (SQLite FTS5, BM25). No embeddings, no GPU.

Text comes straight from text-like files; PDFs and Office documents go through Hermes's own
``tools.read_extract.extract_document_text`` when it is importable (it always is inside the
Hermes process; `mik docs reindex` adds the Hermes checkout to sys.path to get it too).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(os.environ.get("MIKRONOUS_DATA_DIR", "~/.local/share/mikronous")).expanduser()
DB_PATH = DATA_DIR / "docs.sqlite"
TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".org", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini",
            ".cfg", ".log", ".tex", ".html", ".htm", ".xml", ".py", ".js", ".ts", ".sh", ".c", ".h", ".cpp", ".java",
            ".go", ".rs", ".rb", ".php", ".sql"}
DOC_EXT = {".pdf", ".docx", ".doc", ".odt", ".rtf", ".epub", ".pptx", ".ppt", ".odp", ".xlsx", ".xls", ".ods", ".ipynb"}
MAX_BYTES = 25 * 1024 * 1024
MAX_CHARS = 400_000
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".cache", "Trash", ".Trash"}
STALE_SECONDS = 600


def docs_dirs() -> list[Path]:
    raw = os.environ.get("MIKRONOUS_DOCS_DIRS") or _env_from_profile("MIKRONOUS_DOCS_DIRS") or "~/Documents"
    out = []
    for part in raw.split(":"):
        p = Path(part.strip()).expanduser()
        if part.strip() and p.is_dir():
            out.append(p)
    return out


def _env_from_profile(key: str) -> str:
    env_file = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
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
    return con


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


def reindex(force: bool = False, dirs: list[Path] | None = None) -> IndexStats:
    t0 = time.time()
    dirs = dirs if dirs is not None else docs_dirs()
    con = _db()
    known = {r[0]: (r[1], r[2]) for r in con.execute("SELECT path, mtime, size FROM files")}
    seen: set[str] = set()
    st = IndexStats()
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
        st.removed += 1
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
        rows = con.execute(sql, (q, limit)).fetchall()
        if not rows and len(toks) > 1:  # fall back to ANY-term match
            rows = con.execute(sql, (" OR ".join(f'"{t}"' for t in toks), limit)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    con.close()
    return [{"path": r[0], "title": r[1], "score": round(-float(r[2]), 3), "snippet": r[3]} for r in rows]


def stats() -> dict:
    con = _db()
    files = con.execute("SELECT COUNT(*), SUM(CASE WHEN error != '' THEN 1 ELSE 0 END) FROM files").fetchone()
    con.close()
    return {"files": files[0] or 0, "errors": files[1] or 0, "last_reindex": last_reindex(),
            "dirs": [str(d) for d in docs_dirs()], "db": str(DB_PATH)}
