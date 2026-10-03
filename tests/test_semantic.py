"""Semantic file search: chunking, rank fusion, and hybrid search with a toy embedder (no server)."""
import re

import pytest

from mikronous import docs_index as di
from mikronous_model import runner

CONCEPTS = {"car": {"car", "auto", "vehicle", "sedan"}, "insurance": {"insurance", "policy", "coverage", "premium"},
            "renewal": {"renewal", "renew", "expires", "expiry"}, "cake": {"cake", "flour", "sugar", "oven"}}


def toy_embed(texts, kind="document"):
    out = []
    for t in texts:
        words = set(re.findall(r"[a-z]+", t.lower()))
        out.append([float(len(words & vocab)) for vocab in CONCEPTS.values()] or [0.0] * len(CONCEPTS))
    return out


def test_chunking_and_rrf():
    assert di.chunk_text("") == [] and di.chunk_text("short") == ["short"]
    text = ("Alpha paragraph. " * 30 + "\n\n" + "Beta paragraph. " * 30 + "\n\n" + "Gamma paragraph. " * 30)
    chunks = di.chunk_text(text, size=600, overlap=100)
    assert len(chunks) >= 3 and all(len(c) <= 600 for c in chunks)
    assert "".join(chunks).count("Alpha") >= 30            # overlap never drops text
    fused = di.rrf(["a", "b", "c"], ["c", "a"])
    assert max(fused, key=fused.get) == "a" and fused["b"] < fused["c"]


@pytest.fixture
def index(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "policy-notes.txt").write_text("Our auto policy premium is due; the coverage expires in March and must be renewed.")
    (docs / "cake.md").write_text("Mix flour and sugar, bake the cake in the oven for 40 minutes.")
    (docs / "vehicle.txt").write_text("The sedan needs new tyres; the vehicle inspection is Tuesday.")
    monkeypatch.setattr(di, "DATA_DIR", tmp_path)
    monkeypatch.setattr(di, "DB_PATH", tmp_path / "docs.sqlite")
    monkeypatch.setattr(di, "docs_dirs", lambda: [docs])
    monkeypatch.setattr(di, "_embed_env", lambda: {"EMBED_ENABLED": "1"})
    monkeypatch.setattr(di, "_embed_texts", toy_embed)
    return docs


def test_hybrid_search_finds_meaning(index):
    st = di.reindex(dirs=[index])
    assert st.indexed == 3 and st.embedded == 3 and st.embed_skipped == 0
    s = di.stats()
    assert s["semantic"] and s["chunks"] == 3 and s["files_embedded"] == 3
    hits = di.search("car insurance renewal")               # none of these words appear in the file
    assert hits and hits[0]["path"].endswith("policy-notes.txt") and hits[0]["semantic"]
    hits = di.search("cake oven")
    assert hits[0]["path"].endswith("cake.md") and "snippet" in hits[0]


def test_keyword_only_when_server_is_off(index, monkeypatch):
    monkeypatch.setattr(di, "_embed_env", lambda: {})
    st = di.reindex(dirs=[index])
    assert st.embedded == 0 and di.stats()["chunks"] == 0
    assert di.search("tyres")[0]["path"].endswith("vehicle.txt")
    assert di.search("automobile protection") == []           # no vectors, no synonyms


def test_embed_missing_backfills_and_stops_when_server_dies(index, monkeypatch):
    monkeypatch.setattr(di, "_embed_env", lambda: {})
    di.reindex(dirs=[index])                                  # keyword index without chunks
    monkeypatch.setattr(di, "_embed_env", lambda: {"EMBED_ENABLED": "1"})
    st = di.reindex(dirs=[index], embed_missing=True)
    assert st.embedded == 3 and di.stats()["chunks"] == 3
    monkeypatch.setattr(di, "_embed_texts", lambda texts, kind="document": None)
    (index / "new.txt").write_text("brand new file")
    st = di.reindex(dirs=[index])
    assert st.indexed == 1 and st.embed_skipped == 1 and di.search("brand new")[0]["path"].endswith("new.txt")


def test_embed_server_command_line():
    cmd = runner.embed_command_line({"EMBED_SERVER": "/opt/llama/llama-server", "EMBED_MODEL": "/m/nomic.gguf", "EMBED_PORT": "8082"})
    assert cmd[:5] == ["/opt/llama/llama-server", "--host", "127.0.0.1", "--port", "8082"]
    assert "--embeddings" in cmd and cmd[cmd.index("--pooling") + 1] == "mean" and cmd[cmd.index("-ngl") + 1] == "0"
    assert runner.server("embed")["unit"] == "mikronous-embed.service"
    assert "mikronous-embed" in runner.restart_hint("embed") or "mik embed on" in runner.restart_hint("embed")
