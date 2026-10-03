---
name: api-calls
description: Call any HTTP/JSON API with http_request, using keys the user stored by name.
version: 1.0.0
author: Mikronous
license: MIT
platforms: [linux, windows]
metadata:
  hermes:
    tags: [api, http, json, integrations]
    requires_toolsets: [mikronous]
---

# Calling APIs

## When to use
The user names a service with an API (GitHub, a self-hosted app, a public JSON endpoint), or a task
needs structured data that a web page would render with JavaScript.

## Keys
- Keys live in the profile `.env` under a name of the user's choosing, e.g. `GITHUB_TOKEN=…`. You pass only the
  name: `auth_env: "GITHUB_TOKEN"`. The value is added to the request header for you and is never shown.
- If a key is missing, say which name to add to the `.env` and that the gateway needs a restart (`mik update`).
  Never ask the user to paste the key into the chat.
- Defaults: header `Authorization`, scheme `Bearer`. GitHub classic tokens use `auth_scheme: "token"`; some
  services want a custom header: `auth_header: "X-Api-Key"`, `auth_scheme: ""`.

## Procedure
1. Read first: `http_request` with `GET` and the exact URL. Parse the `json` field of the result; it is already decoded.
2. Writes (`POST`, `PUT`, `PATCH`, `DELETE`): state what you are about to send and to where, wait for the user's
   yes, then call again with `confirm: true`.
3. Report the few fields the user asked for, not the whole payload. Mention the HTTP status only when it is not 2xx.

## Examples
- Stars of a repo: `GET https://api.github.com/repos/<owner>/<repo>` → `json.stargazers_count`.
- Open issues assigned to me (needs `GITHUB_TOKEN`): `GET https://api.github.com/issues?filter=assigned`, `auth_env: GITHUB_TOKEN`.
- Create an issue: `POST https://api.github.com/repos/<owner>/<repo>/issues`, `json: {"title": "...", "body": "..."}`, `auth_env: GITHUB_TOKEN`, `confirm: true` after the user agreed.

## Pitfalls
- 401/403: the key is missing, wrong, or lacks scope; say so, do not retry blindly.
- Large responses are truncated at `char_limit`; narrow the request (query parameters, pagination) instead of raising the limit.
