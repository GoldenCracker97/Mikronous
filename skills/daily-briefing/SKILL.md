---
name: daily-briefing
description: Short morning briefing from the user's open notes, due items and reminders.
version: 1.0.0
author: Mikronous
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [productivity, notes, briefing]
    requires_toolsets: [mikronous, cronjob]
---

# Daily briefing

## When to use
The user asks for a briefing, "what's on today", "catch me up", or a cron job named daily-briefing fires.

## Procedure
1. `notes_manage` with `action: list`, `status: open` — collect open items; note any with `due` today or overdue.
2. `cronjob_manage` with `action: list` — upcoming reminders in the next 24 h.
3. If the user keeps a calendar export or journal in their documents, `docs_search` for today's date (`YYYY-MM-DD`) and skim the best hit with `read_file`.
4. Write at most eight lines: overdue first, then due today, then upcoming reminders, then one suggested first task. No preamble.
5. When invoked by cron, deliver with `desktop_notify` (title "Good morning", body = the briefing) in addition to the reply.

## Pitfalls
- Do not invent items. If a list is empty, say "nothing open".
- Keep each line under ~90 characters; it is read in a small window or a notification.

## Verification
Every item mentioned exists in `notes_manage list` or `cronjob_manage list` output from this session.
