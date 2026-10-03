"""`mik update` — pull the latest Mikronous from GitHub and re-apply the install.

    mik update            pull, re-run the installer (no model download), restart the tray
    mik update --check    only report whether updates are available
    mik update --pull     pull only; skip the installer and the tray restart

Works from an editable install (the normal case: `mik` points at the git checkout). Local
modifications are never overwritten: the pull is fast-forward only and stops with a message
if the checkout is dirty or has diverged.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .platform import IS_WINDOWS

REPO_DIR = Path(__file__).resolve().parent.parent


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO_DIR), *args], capture_output=True, text=True, check=check)


def repo_ok() -> str | None:
    if not (REPO_DIR / ".git").exists():
        return (f"{REPO_DIR} is not a git checkout. `mik update` needs the editable install that scripts/install.sh "
                "creates; otherwise: git clone https://github.com/GoldenCracker97/Mikronous && cd Mikronous && scripts/install.sh")
    try:
        _git("rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"git is not usable here: {exc}"
    return None


def status() -> dict:
    """Fetch and compare: {'branch', 'local', 'remote', 'behind', 'ahead', 'dirty', 'commits': [...]}"""
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git("fetch", "--quiet", "origin", branch)
    local = _git("rev-parse", "HEAD").stdout.strip()
    remote = _git("rev-parse", f"origin/{branch}").stdout.strip()
    counts = _git("rev-list", "--left-right", "--count", f"HEAD...origin/{branch}").stdout.split()
    ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no").stdout.strip())
    commits = _git("log", "--oneline", f"HEAD..origin/{branch}").stdout.strip().splitlines() if behind else []
    return {"branch": branch, "local": local[:7], "remote": remote[:7], "behind": behind, "ahead": ahead,
            "dirty": dirty, "commits": commits}


def pull(st: dict) -> bool:
    if st["dirty"]:
        print("local changes in the checkout; commit or stash them first (git -C %s status)" % REPO_DIR, file=sys.stderr)
        return False
    if st["ahead"]:
        print(f"the checkout has {st['ahead']} local commit(s) not on origin/{st['branch']}; merge or rebase by hand", file=sys.stderr)
        return False
    res = _git("pull", "--ff-only", "origin", st["branch"], check=False)
    if res.returncode != 0:
        print(res.stderr.strip() or res.stdout.strip(), file=sys.stderr)
        return False
    return True


def reinstall() -> bool:
    """Re-run the installer in update mode: config, plugin links, mik deps, gateway restart. No model download."""
    if IS_WINDOWS:
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO_DIR / "scripts" / "install.ps1"), "-NoModel", "-NoTray"]
    else:
        cmd = ["bash", str(REPO_DIR / "scripts" / "install.sh"), "--no-model", "--no-tray"]
    print(f"re-running the installer: {' '.join(cmd[-3:])}")
    return subprocess.run(cmd).returncode == 0


def restart_tray() -> None:
    """Quit the running tray (if any) and start a fresh one, detached, so the new code is live."""
    mik = Path(sys.argv[0]).resolve() if Path(sys.argv[0]).name.startswith("mik") else None
    tray_cmd = [str(mik), "show"] if mik and mik.exists() else [sys.executable, "-m", "mikronous_tray", "show"]
    quit_cmd = [str(mik), "quit-tray"] if mik and mik.exists() else [sys.executable, "-m", "mikronous_tray", "quit"]
    env = {**os.environ, "MIKRONOUS_KEEP_MODEL": "1"}   # an update must not unload the model
    subprocess.run(quit_cmd, env=env, capture_output=True)
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(tray_cmd, **kwargs)


def main(argv: list[str]) -> int:
    check_only, pull_only = "--check" in argv, "--pull" in argv
    err = repo_ok()
    if err:
        print(err, file=sys.stderr)
        return 1
    try:
        st = status()
    except subprocess.CalledProcessError as exc:
        print(f"could not contact origin: {exc.stderr.strip() or exc}", file=sys.stderr)
        return 1
    from . import __version__
    print(f"Mikronous {__version__} on branch {st['branch']} ({st['local']}); origin/{st['branch']} is {st['remote']}")
    if not st["behind"]:
        print("up to date")
        if check_only or pull_only:
            return 0
    else:
        print(f"{st['behind']} new commit(s):")
        for line in st["commits"]:
            print("  " + line)
        if check_only:
            return 0
        if not pull(st):
            return 1
        print("pulled")
        if pull_only:
            return 0
    ok = reinstall()
    if not ok:
        print("installer reported problems; see the output above", file=sys.stderr)
    restart_tray()
    try:
        new_version = subprocess.run([sys.executable, "-c", "import mikronous_cli; print(mikronous_cli.__version__)"],
                                     capture_output=True, text=True, cwd=str(REPO_DIR)).stdout.strip()
    except OSError:
        new_version = "?"
    print(f"update {'complete' if ok else 'finished with problems'}: Mikronous {new_version}; the tray was restarted")
    return 0 if ok else 1
