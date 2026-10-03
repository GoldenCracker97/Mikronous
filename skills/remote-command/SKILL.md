---
name: remote-command
description: Run a command on another machine over SSH with the user's existing keys and host aliases.
version: 1.0.0
author: Mikronous
license: MIT
platforms: [linux, windows]
metadata:
  hermes:
    tags: [ssh, remote, servers]
    requires_toolsets: [terminal]
---

# Commands on other machines

## When to use
"Run X on the server", "What's the uptime of my NAS?", "Pull the latest code on the Pi", "Copy that log from the laptop."

## Procedure
1. Use the `terminal` tool with `ssh`. Rely on the user's own `~/.ssh/config` aliases and keys; never type or ask for passwords.
   `ssh -o BatchMode=yes -o ConnectTimeout=5 <host> '<command>'`
   Files: `scp -o BatchMode=yes <host>:<remote path> <local path>` (or the other way round).
2. Before anything that changes the remote machine (installs, restarts, deletes, config edits), state the exact command and wait for the user's yes. Read-only commands (`uptime`, `df -h`, `systemctl status`, `tail`, `ls`) can run directly.
3. Relay the output trimmed to what was asked; name the host.

## Pitfalls
- `Permission denied (publickey)` means no key for that host: tell the user, do not retry with passwords.
- Unknown host alias: suggest adding it to `~/.ssh/config` rather than guessing IPs. `lan_devices` can help find the IP.
- Long-running jobs: use `nohup … &` or `tmux` and come back; never leave the terminal blocked.
