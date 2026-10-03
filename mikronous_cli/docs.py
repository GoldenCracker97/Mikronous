"""`mik docs` — manage the document index behind the agent's docs_search tool."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def _docs_index():
    """Import hermes_plugin/mikronous/docs_index.py standalone (the plugin is not a Python package of ours)."""
    path = REPO_DIR / "hermes_plugin" / "mikronous" / "docs_index.py"
    spec = importlib.util.spec_from_file_location("mikronous_docs_index", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod  # dataclasses need the module registered before the body runs
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    di = _docs_index()
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        s = di.stats()
        age = f"{(time.time() - s['last_reindex']) / 60:.0f} min ago" if s["last_reindex"] else "never"
        print(f"folders   {', '.join(s['dirs']) or '(none exist; set MIKRONOUS_DOCS_DIRS)'}")
        print(f"indexed   {s['files']} files ({s['errors']} with extraction errors)")
        print(f"refreshed {age}\nindex     {s['db']}")
        return 0
    if cmd == "reindex":
        force = "--force" in argv
        print(f"indexing {', '.join(str(d) for d in di.docs_dirs())} ...", file=sys.stderr)
        st = di.reindex(force=force)
        print(f"scanned {st.scanned}, indexed {st.indexed}, removed {st.removed}, errors {st.errors}, {st.seconds}s")
        if st.errors:
            print("(PDF/Office extraction needs Hermes's extractor; inside Hermes sessions those files index fine)")
        return 0
    if cmd == "search" and len(argv) > 1:
        for h in di.search(" ".join(argv[1:])):
            print(f"{h['score']:6.2f}  {h['path']}\n        {h['snippet']}")
        return 0
    print("usage: mik docs [status|reindex [--force]|search <query>]", file=sys.stderr)
    return 2
