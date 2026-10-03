"""Start, stop and inspect the local llama-server, whichever way this OS runs it.

Linux: the systemd user unit ``mikronous-llama.service`` (see systemd/).
Windows: a detached ``llama-server.exe`` started from the same ``llama.env`` settings, tracked by a
pid file under the config dir, logging to ``llama-server.log`` there. Both read the flags from
``llama.env`` so `mik model` keeps working unchanged.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from mikronous_cli.paths import LLAMA_ENV, read_env
from mikronous_cli.platform import IS_WINDOWS, conf_dir

UNIT = "mikronous-llama.service"
PID_FILE = conf_dir() / "llama-server.pid"
LOG_FILE = conf_dir() / "llama-server.log"

# A second, CPU-only llama-server provides embeddings for semantic file search (mikronous_cli.embed).
SERVERS = {
    "llama": {"unit": UNIT, "pid": PID_FILE, "log": LOG_FILE},
    "embed": {"unit": "mikronous-embed.service", "pid": conf_dir() / "embed-server.pid", "log": conf_dir() / "embed-server.log"},
}


def server(name: str) -> dict:
    return SERVERS[name]


def embed_command_line(env: dict[str, str]) -> list[str]:
    ctx = env.get("EMBED_CTX", "2048")
    return [env.get("EMBED_SERVER", "llama-server"), "--host", "127.0.0.1", "--port", env.get("EMBED_PORT", "8082"),
            "-m", env.get("EMBED_MODEL", ""), "--alias", "mikronous-embed", "--embeddings", "--pooling", env.get("EMBED_POOLING", "mean"),
            "-c", ctx, "-ub", ctx, "-b", ctx, "-ngl", "0", "-t", env.get("EMBED_THREADS", "4")]


def command_line(env: dict[str, str] | None = None) -> list[str]:
    """The llama-server invocation the systemd unit uses, built from llama.env."""
    env = env if env is not None else read_env(LLAMA_ENV)
    cmd = [env.get("LLAMA_SERVER", "llama-server"), "--host", "127.0.0.1", "--port", env.get("LLAMA_PORT", "8081"),
           "-m", env.get("LLAMA_MODEL", ""), "--alias", env.get("LLAMA_ALIAS", "mikronous-local"),
           "-c", env.get("LLAMA_CTX", "65536"), "-ngl", env.get("LLAMA_NGL", "99"), "-t", env.get("LLAMA_THREADS", "4"),
           "-ctk", env.get("LLAMA_KV_K", "q8_0"), "-ctv", env.get("LLAMA_KV_V", "q8_0"), "-fa", "on", "--jinja",
           "--parallel", env.get("LLAMA_PARALLEL", "1")]
    extra = env.get("LLAMA_EXTRA_ARGS", "").strip()
    if extra:
        cmd += shlex.split(extra, posix=not IS_WINDOWS)
    if env.get("LLAMA_MMPROJ", "").strip():
        cmd += ["--mmproj", env["LLAMA_MMPROJ"].strip()]
    return cmd


def port() -> str:
    return read_env(LLAMA_ENV).get("LLAMA_PORT", "8081")


def answers(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port()}/v1/models", timeout=timeout):  # noqa: S310
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


# ----------------------------------------------------------------------------- systemd (Linux)
def _systemctl(*args: str) -> subprocess.CompletedProcess | None:
    if not shutil.which("systemctl"):
        return None
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None


# ----------------------------------------------------------------------------- pid file (Windows)
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid() -> int:
    try:
        return int(PID_FILE.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


# ----------------------------------------------------------------------------- public API
def available() -> bool:
    """Can this machine start/stop the server at all?"""
    return IS_WINDOWS or shutil.which("systemctl") is not None


def mode() -> str:
    return "process" if IS_WINDOWS else "systemd"


def _read_pid_of(name: str) -> int:
    try:
        return int(SERVERS[name]["pid"].read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def _cmd_for(name: str) -> list[str]:
    if name == "embed":
        from mikronous_cli.embed import env as embed_env
        return embed_command_line(embed_env())
    return command_line(read_env(LLAMA_ENV))


def is_running(name: str = "llama") -> bool:
    if IS_WINDOWS:
        return _pid_alive(_read_pid_of(name)) or (answers() if name == "llama" else False)
    r = _systemctl("is-active", SERVERS[name]["unit"])
    return bool(r) and r.stdout.strip() in ("active", "activating")


def start(name: str = "llama") -> bool:
    srv = SERVERS[name]
    if IS_WINDOWS:
        if is_running(name):
            return True
        cmd = _cmd_for(name)
        exe = Path(cmd[0])
        if not exe.exists():
            print(f"llama-server not found: {exe}", file=sys.stderr)
            return False
        conf_dir().mkdir(parents=True, exist_ok=True)
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        log = open(srv["log"], "ab")  # noqa: SIM115 - handed to the child
        try:
            proc = subprocess.Popen(cmd, cwd=str(exe.parent), stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        except OSError as exc:
            print(f"could not start llama-server: {exc}", file=sys.stderr)
            return False
        srv["pid"].write_text(str(proc.pid))
        return True
    _systemctl("daemon-reload")
    r = _systemctl("start", srv["unit"])
    return bool(r) and r.returncode == 0


def stop(name: str = "llama") -> bool:
    srv = SERVERS[name]
    if IS_WINDOWS:
        pid = _read_pid_of(name)
        if pid and _pid_alive(pid):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        try:
            srv["pid"].unlink()
        except OSError:
            pass
        return True
    r = _systemctl("stop", srv["unit"])
    return bool(r) and r.returncode == 0


def restart(name: str = "llama") -> bool:
    if IS_WINDOWS:
        stop(name)
        time.sleep(1.0)
        return start(name)
    _systemctl("daemon-reload")
    r = _systemctl("restart", SERVERS[name]["unit"])
    return bool(r) and r.returncode == 0


def wait_until_up(timeout: float = 180.0, progress=None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if answers(3.0):
            return True
        if not is_running():
            return False
        if progress:
            progress()
        time.sleep(2)
    return False


def recent_log(lines: int = 25, name: str = "llama") -> str:
    srv = SERVERS[name]
    if IS_WINDOWS:
        try:
            return "\n".join(srv["log"].read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""
    r = subprocess.run(["journalctl", "--user", "-u", srv["unit"], "-n", str(lines), "--no-pager"], capture_output=True, text=True)
    return r.stdout


def restart_hint(name: str = "llama") -> str:
    if IS_WINDOWS:
        return "mik model restart" if name == "llama" else "mik embed on"
    return f"systemctl --user restart {SERVERS[name]['unit'].removesuffix('.service')}"
