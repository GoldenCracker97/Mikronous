"""`mik docs` — manage the document index behind the agent's docs_search tool."""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def _docs_index():
    """Import the plugin's docs_index as part of the ``mikronous`` package (it uses relative imports), the same
    way the tests do: hermes_plugin/ goes on sys.path. Nothing else of ours is called ``mikronous``."""
    plugin_root = str(REPO_DIR / "hermes_plugin")
    if plugin_root not in sys.path:
        sys.path.insert(0, plugin_root)
    from mikronous import docs_index  # type: ignore
    return docs_index


def main(argv: list[str]) -> int:
    di = _docs_index()
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        s = di.stats()
        age = f"{(time.time() - s['last_reindex']) / 60:.0f} min ago" if s["last_reindex"] else "never"
        print(f"folders   {', '.join(s['dirs']) or '(none exist; set MIKRONOUS_DOCS_DIRS)'}")
        print(f"indexed   {s['files']} files ({s['errors']} with extraction errors)")
        print(f"refreshed {age}\nindex     {s['db']}")
        print(f"semantic  {'on: ' + str(s['chunks']) + ' chunks over ' + str(s['files_embedded']) + ' files' if s['semantic'] else 'off (mik embed on)'}")
        return 0
    if cmd == "reindex":
        force = "--force" in argv
        print(f"indexing {', '.join(str(d) for d in di.docs_dirs())} ...", file=sys.stderr)
        st = di.reindex(force=force, embed_missing="--embed" in argv or di.embed_enabled())
        print(f"scanned {st.scanned}, indexed {st.indexed}, removed {st.removed}, errors {st.errors}, {st.seconds}s"
              + (f", embedded {st.embedded}" if st.embedded or st.embed_skipped else "")
              + (" (embedding server not answering; run mik embed status)" if st.embed_skipped else ""))
        if st.errors:
            print("(PDF/Office extraction needs Hermes's extractor; inside Hermes sessions those files index fine)")
        return 0
    if cmd == "search" and len(argv) > 1:
        for h in di.search(" ".join(argv[1:])):
            print(f"{h['score']:6.2f}  {h['path']}\n        {h['snippet']}")
        return 0
    print("usage: mik docs [status|reindex [--force] [--embed]|search <query>]", file=sys.stderr)
    return 2
