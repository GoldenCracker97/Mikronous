---
name: skill-authoring
description: Save a procedure the user taught you as a reusable skill, with their consent.
version: 1.0.0
author: Mikronous
license: MIT
platforms: [linux, windows]
metadata:
  hermes:
    tags: [skills, learning]
    requires_toolsets: [skills]
---

# Learning new skills

## When to offer
- The user explains how a service, API or device of theirs works (endpoints, entity names, host aliases, a quirk).
- The same multi-step task comes up a second time.
- The user says "remember how to do this" or "save this as a skill".

## Procedure
1. Offer in one line: "Want me to save this as a skill so I do it the same way next time?" Only continue on a yes.
2. `skill_manage` with `action: create`, a short lowercase name, and a SKILL.md with: when to use, the exact
   steps and tool calls that worked (URLs, entity ids, host aliases), and the pitfalls you hit. Keep it under
   40 lines. Put key *names* (e.g. `GITHUB_TOKEN`) in the skill, never key values.
3. Tell the user the skill's name and that it can be edited or removed with `skill_manage` or by deleting its folder.
4. Next time the topic comes up, `skill_view` it first.

## Pitfalls
- Do not create skills for one-off questions or for things Hermes already has a tool for.
- Never store secrets, personal data about third parties, or credentials in a skill.
