---
name: file-qa
description: Answer questions from the user's own documents with cited file paths.
version: 1.0.0
author: Mikronous
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [documents, search, rag]
    requires_toolsets: [mikronous, file]
---

# Questions about the user's files

## When to use
The user asks what a document says, where something is written down, or anything that should be answered from their own files rather than the web.

## Procedure
1. `docs_search` with 2–5 distinctive keywords from the question (names, numbers, product names). Avoid stop words.
2. Pick the top 1–3 hits whose snippet looks relevant. `read_file` each one (use `offset`/`limit` for long files; the snippet tells you roughly where to look).
3. Answer from the text you actually read. Quote the key phrase when it matters.
4. End with the file path(s) you used, one per line, prefixed `Source:`.
5. If nothing relevant is found, say so and offer to search the web only if the user wants.

## Pitfalls
- `docs_search` only knows folders listed in `MIKRONOUS_DOCS_DIRS` (default `~/Documents`). If the user names a folder outside it, use `search_files` / `read_file` directly.
- Never answer from the snippet alone; it is 24 words of context.

## Verification
Each claim traces to a `read_file` result from this session.
