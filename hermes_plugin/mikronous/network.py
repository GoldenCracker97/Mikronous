"""Network tools: devices on the LAN, host checks, Wake-on-LAN, and a generic HTTP client.

Every function takes the tool's ``args`` dict and returns a JSON-serialisable dict; nothing raises
on a missing binary or an unreachable host. Secrets for ``http_request`` are referenced by the NAME
of a variable in the profile's ``.env`` and are never included in results.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

IS_WINDOWS = sys.platform == "win32"
_MAC_RE = re.compile(r"([0-9a-f]{2}[:-]){5}[0-9a-f]{2}", re.IGNORECASE)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd: list[str], timeout: float = 15.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# ----------------------------------------------------------------------------- LAN discovery
def parse_ip_neigh(text: str) -> list[dict]:
    """Linux ``ip neigh``: ``192.168.1.5 dev wlan0 lladdr aa:bb:... REACHABLE``."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        ip = parts[0]
        mac = parts[parts.index("lladdr") + 1].lower() if "lladdr" in parts and parts.index("lladdr") + 1 < len(parts) else ""
        state = parts[-1] if parts[-1].isupper() else ""
        if state in ("FAILED", "INCOMPLETE") or not mac:
            continue
        out.append({"ip": ip, "mac": mac, "state": state.lower()})
    return out


def parse_arp_a(text: str) -> list[dict]:
    """Windows ``arp -a``: ``  192.168.1.5          aa-bb-cc-dd-ee-ff     dynamic``."""
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f-]{17})\s+(\w+)", line, re.IGNORECASE)
        if not m:
            continue
        ip, mac, kind = m.group(1), m.group(2).replace("-", ":").lower(), m.group(3).lower()
        if kind != "dynamic" or mac in ("ff:ff:ff:ff:ff:ff",) or ip.startswith(("224.", "239.", "255.")):
            continue
        out.append({"ip": ip, "mac": mac, "state": kind})
    return out


def _local_networks() -> list[ipaddress.IPv4Network]:
    nets: list[ipaddress.IPv4Network] = []
    if IS_WINDOWS:
        for m in re.finditer(r"IPv4 Address[ .:]*(\d+\.\d+\.\d+\.\d+)\s+Subnet Mask[ .:]*(\d+\.\d+\.\d+\.\d+)", _run(["ipconfig"]), re.S):
            try:
                nets.append(ipaddress.IPv4Interface(f"{m.group(1)}/{m.group(2)}").network)
            except ValueError:
                pass
    else:
        for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", _run(["ip", "-4", "-o", "addr"])):
            try:
                net = ipaddress.IPv4Interface(m.group(1)).network
                if not net.is_loopback:
                    nets.append(net)
            except ValueError:
                pass
    return [n for n in nets if n.prefixlen >= 22]   # never sweep more than ~1000 addresses


def _ping(host: str, timeout_s: float = 1.0) -> float | None:
    """Round-trip in ms, or None."""
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout_s * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout_s))), host]
    t0 = time.monotonic()
    try:
        ok = subprocess.run(cmd, capture_output=True, timeout=timeout_s + 2, creationflags=_NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None
    return round((time.monotonic() - t0) * 1000, 1) if ok else None


def _reverse_name(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        pass
    if shutil.which("avahi-resolve-address"):
        out = _run(["avahi-resolve-address", ip], timeout=2)
        parts = out.split()
        if len(parts) >= 2:
            return parts[1].rstrip(".")
    return ""


def lan_devices(args: dict, **_: Any) -> dict:
    scan = bool(args.get("scan", False))
    swept = 0
    if scan:
        hosts = [str(h) for net in _local_networks() for h in net.hosts()]
        swept = len(hosts)
        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
            list(pool.map(lambda h: _ping(h, 1.0), hosts))
    if IS_WINDOWS:
        devices = parse_arp_a(_run(["arp", "-a"]))
    else:
        devices = parse_ip_neigh(_run(["ip", "neigh"])) if shutil.which("ip") else parse_arp_a(_run(["arp", "-a"]))
    seen: dict[str, dict] = {}
    for d in devices:
        seen.setdefault(d["ip"], d)
    devices = sorted(seen.values(), key=lambda d: tuple(int(x) for x in d["ip"].split(".")))
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        names = list(pool.map(lambda d: _reverse_name(d["ip"]), devices))
    for d, n in zip(devices, names):
        d["name"] = n
    return {"count": len(devices), "devices": devices, "swept_addresses": swept,
            "hint": "names come from DNS/mDNS; unnamed entries are often phones, IoT or the router. "
                    "Use scan=true for a fresh sweep (takes a few seconds)."}


# ----------------------------------------------------------------------------- host check
def host_check(args: dict, **_: Any) -> dict:
    host = str(args.get("host") or "").strip()
    if not host:
        return {"error": "host is required (name or IP)"}
    ports = args.get("ports") or []
    try:
        ports = [int(p) for p in ports][:20]
    except (TypeError, ValueError):
        return {"error": "ports must be a list of integers"}
    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror:
        return {"host": host, "resolved": False, "reachable": False, "error": "name does not resolve"}
    rtt = _ping(ip, 1.5)
    open_ports, closed = [], []
    for p in ports:
        try:
            with socket.create_connection((ip, p), timeout=2.0):
                open_ports.append(p)
        except OSError:
            closed.append(p)
    return {"host": host, "ip": ip, "resolved": True, "reachable": rtt is not None or bool(open_ports),
            "ping_ms": rtt, "open_ports": open_ports, "closed_ports": closed}


# ----------------------------------------------------------------------------- wake on lan
def magic_packet(mac: str) -> bytes:
    clean = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(clean) != 12:
        raise ValueError(f"not a MAC address: {mac}")
    return b"\xff" * 6 + bytes.fromhex(clean) * 16


def wake_on_lan(args: dict, **_: Any) -> dict:
    mac = str(args.get("mac") or "").strip()
    broadcast = str(args.get("broadcast") or "255.255.255.255").strip()
    try:
        pkt = magic_packet(mac)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for port in (9, 7):
                s.sendto(pkt, (broadcast, port))
    except OSError as exc:
        return {"error": f"could not send magic packet: {exc}"}
    return {"ok": True, "mac": mac.lower(), "broadcast": broadcast,
            "note": "the machine must have Wake-on-LAN enabled in its firmware/OS; give it 30-60 s"}


# ----------------------------------------------------------------------------- http
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_RESPONSE_HEADERS = ("content-type", "content-length", "date", "etag", "last-modified", "location", "x-ratelimit-remaining")


def _secret(name: str) -> str:
    try:
        from agent.secret_scope import get_secret  # type: ignore
        val = get_secret(name, "") or ""
        if val:
            return val
    except Exception:  # noqa: BLE001 - outside Hermes, fall back to the environment
        pass
    return os.environ.get(name, "")


def http_request(args: dict, **_: Any) -> dict:
    method = str(args.get("method") or "GET").upper()
    url = str(args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"error": "only http:// and https:// URLs are allowed"}
    if method not in _SAFE_METHODS and not args.get("confirm"):
        return {"error": f"{method} changes something on {parsed.netloc}. Ask the user to confirm, then call again with confirm=true."}
    headers = {str(k): str(v) for k, v in (args.get("headers") or {}).items()}
    headers.setdefault("User-Agent", "Mikronous/0.1 (local desktop assistant)")
    auth_env = str(args.get("auth_env") or "").strip()
    secret = ""
    if auth_env:
        secret = _secret(auth_env)
        if not secret:
            return {"error": f"{auth_env} is not set in the profile .env; add it there (never paste the value into chat)"}
        scheme = str(args.get("auth_scheme") if args.get("auth_scheme") is not None else "Bearer").strip()
        headers[str(args.get("auth_header") or "Authorization")] = f"{scheme} {secret}".strip()
    data: bytes | None = None
    if args.get("json") is not None:
        data = json.dumps(args["json"]).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif args.get("body") is not None:
        data = str(args["body"]).encode("utf-8")
    timeout = float(args.get("timeout") or 30)
    limit = int(args.get("char_limit") or 20000)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - the user named the URL
            status, raw, rheaders = resp.status, resp.read(limit * 4 + 1), dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        status, raw, rheaders = exc.code, exc.read(limit * 4 + 1), dict(exc.headers.items()) if exc.headers else {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"error": f"request failed: {exc.__class__.__name__}: {getattr(exc, 'reason', exc)}"}
    text = raw.decode("utf-8", "replace")
    if secret:  # the secret never appears in what the model sees, even if the server echoes it
        text = text.replace(secret, "[redacted]")
    truncated = len(text) > limit
    text = text[:limit]
    result: dict[str, Any] = {"status": status, "ok": 200 <= status < 300, "method": method, "url": url,
                              "elapsed_ms": round((time.monotonic() - t0) * 1000),
                              "headers": {k: v for k, v in rheaders.items() if k.lower() in _RESPONSE_HEADERS},
                              "truncated": truncated}
    try:
        result["json"] = json.loads(text) if not truncated else None
        if result["json"] is None:
            del result["json"]
            result["text"] = text
    except ValueError:
        result["text"] = text
    return result
