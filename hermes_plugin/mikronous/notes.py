"""Durable notes and to-dos: one markdown file per note under ~/Mikronous/notes, FTS5 index beside them.

File format (human-editable):

    ---
    id: 20261003-153012-a1b2
    title: Buy milk
    created: 2026-10-03T15:30:12
    tags: [shopping]
    done: false
    due: 2026-10-04
    ---
    body text ...
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

NOTES_DIR = Path(os.environ.get("MIKRONOUS_NOTES_DIR", "~/Mikronous/notes")).expanduser()
DB_PATH = Path(os.environ.get("MIKRONOUS_DATA_DIR", "~/.local/share/mikronous")).expanduser() / "notes.sqlite"
_HEADER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


@dataclass
class Note:
    id: str
    title: str
    created: str
    tags: list[str] = field(default_factory=list)
    done: bool = False
    due: str = ""
    body: str = ""
    path: Path | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "created": self.created, "tags": self.tags, "done": self.done,
                "due": self.due, "body": self.body, "path": str(self.path) if self.path else ""}

    def render(self) -> str:
        tags = "[" + ", ".join(self.tags) + "]"
        head = (f"---\nid: {self.id}\ntitle: {self.title}\ncreated: {self.created}\ntags: {tags}\n"
                f"done: {'true' if self.done else 'false'}\ndue: {self.due}\n---\n")
        return head + (self.body.rstrip() + "\n" if self.body else "")


def _parse(path: Path) -> Note | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    m = _HEADER_RE.match(text)
    meta: dict[str, str] = {}
    body = text
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    tags_raw = meta.get("tags", "").strip("[] ")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    return Note(id=meta.get("id") or path.stem, title=meta.get("title") or path.stem,
                created=meta.get("created") or datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                tags=tags, done=meta.get("done", "false").lower() == "true", due=meta.get("due", ""),
                body=body.strip(), path=path)


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40] or "note"


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, id TEXT)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(id UNINDEXED, title, tags, body, tokenize='porter unicode61')")
    return con


def reindex(force: bool = False) -> int:
    """Bring the FTS index in line with the files on disk. Returns the number of files (re)indexed."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    con = _db()
    known = {row[0]: row[1] for row in con.execute("SELECT path, mtime FROM files")}
    seen: set[str] = set()
    changed = 0
    for path in sorted(NOTES_DIR.glob("*.md")):
        key = str(path)
        seen.add(key)
        mtime = path.stat().st_mtime
        if not force and known.get(key) == mtime:
            continue
        note = _parse(path)
        if not note:
            continue
        con.execute("DELETE FROM notes_fts WHERE id = ?", (note.id,))
        con.execute("INSERT INTO notes_fts (id, title, tags, body) VALUES (?, ?, ?, ?)",
                    (note.id, note.title, " ".join(note.tags), note.body))
        con.execute("INSERT OR REPLACE INTO files (path, mtime, id) VALUES (?, ?, ?)", (key, mtime, note.id))
        changed += 1
    for gone in set(known) - seen:
        nid = con.execute("SELECT id FROM files WHERE path = ?", (gone,)).fetchone()
        if nid:
            con.execute("DELETE FROM notes_fts WHERE id = ?", (nid[0],))
        con.execute("DELETE FROM files WHERE path = ?", (gone,))
    con.commit()
    con.close()
    return changed


def all_notes() -> list[Note]:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    notes = [n for n in (_parse(p) for p in NOTES_DIR.glob("*.md")) if n]
    notes.sort(key=lambda n: n.created, reverse=True)
    return notes


def get(note_id: str) -> Note | None:
    note_id = (note_id or "").strip()
    for n in all_notes():
        if n.id == note_id or n.id.startswith(note_id) and len(note_id) >= 4:
            return n
    return None


def add(title: str, body: str = "", tags: list[str] | None = None, due: str = "") -> Note:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    nid = f"{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    title = (title or body.splitlines()[0] if body else "Untitled").strip()[:120]
    note = Note(id=nid, title=title, created=now.isoformat(timespec="seconds"), tags=[t.strip() for t in (tags or []) if t.strip()],
                done=False, due=(due or "").strip(), body=(body or "").strip())
    note.path = NOTES_DIR / f"{nid}-{_slug(title)}.md"
    note.path.write_text(note.render(), encoding="utf-8")
    reindex()
    return note


def save(note: Note) -> Note:
    assert note.path is not None
    note.path.write_text(note.render(), encoding="utf-8")
    reindex()
    return note


def delete(note_id: str) -> bool:
    n = get(note_id)
    if not n or not n.path:
        return False
    n.path.unlink(missing_ok=True)
    reindex()
    return True


def list_notes(status: str = "open", tag: str = "") -> list[Note]:
    out = []
    for n in all_notes():
        if status == "open" and n.done:
            continue
        if status == "done" and not n.done:
            continue
        if tag and tag.lower() not in [t.lower() for t in n.tags]:
            continue
        out.append(n)
    return out


def search(query: str, limit: int = 10) -> list[tuple[Note, float]]:
    reindex()
    con = _db()
    q = " ".join(f'"{tok}"' for tok in re.findall(r"\w+", query)) or '""'
    try:
        rows = con.execute("SELECT id, bm25(notes_fts) FROM notes_fts WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT ?",
                           (q, limit)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    con.close()
    by_id = {n.id: n for n in all_notes()}
    return [(by_id[r[0]], float(r[1])) for r in rows if r[0] in by_id]
