---
name: lan
description: See what is on the local network, check whether a host is up, and wake machines.
version: 1.0.0
author: Mikronous
license: MIT
platforms: [linux, windows]
metadata:
  hermes:
    tags: [network, lan, devices]
    requires_toolsets: [mikronous]
---

# Local network

## When to use
"What's on my network?", "Is the NAS/router/printer up?", "What's the IP of my laptop?", "Wake up my desktop."

## Procedure
- **List devices**: `lan_devices`. If the list looks stale or short, call it again with `scan: true` (pings the subnet, a few seconds). Report IP, name (when known) and MAC in a short table; say that unnamed entries are usually phones, IoT gear or the router.
- **Is X up?**: `host_check` with the name or IP and, when it matters, the ports: 22 (SSH), 80/443 (web), 445 (SMB), 631 (printer), 8123 (Home Assistant). Answer in one line: up/down, latency, which ports answered.
- **Wake a machine**: `wake_on_lan` needs the MAC. If you do not have it, check `notes_manage` (search "MAC") first, then `lan_devices` (only awake machines appear), then ask the user. After waking, offer to save the MAC as a note so you never ask again.

## Pitfalls
- Only the user's own network. Never scan addresses outside the local subnet.
- A machine that does not answer ping may still be up (firewalls); say "no answer to ping" rather than "off".
