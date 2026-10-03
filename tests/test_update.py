import subprocess
from pathlib import Path

import pytest

from mikronous_cli import update


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@x", "PATH": __import__("os").environ["PATH"], "HOME": str(cwd)})


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "Main", str(origin)], check=True)
    work = tmp_path / "upstream"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    (work / "f.txt").write_text("1")
    _git(work, "add", "."); _git(work, "commit", "-q", "-m", "one"); _git(work, "push", "-q", "origin", "HEAD:Main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "-b", "Main", str(origin), str(clone)], check=True)
    return work, clone


def test_status_and_pull(repos, monkeypatch):
    work, clone = repos
    monkeypatch.setattr(update, "REPO_DIR", clone)
    assert update.repo_ok() is None
    st = update.status()
    assert st["behind"] == 0 and st["branch"] == "Main"
    (work / "f.txt").write_text("2"); _git(work, "commit", "-qam", "two"); _git(work, "push", "-q", "origin", "HEAD:Main")
    st = update.status()
    assert st["behind"] == 1 and st["commits"][0].endswith("two") and not st["dirty"]
    assert update.pull(st)
    assert (clone / "f.txt").read_text() == "2"


def test_pull_refuses_dirty_checkout(repos, monkeypatch):
    work, clone = repos
    monkeypatch.setattr(update, "REPO_DIR", clone)
    (work / "f.txt").write_text("3"); _git(work, "commit", "-qam", "three"); _git(work, "push", "-q", "origin", "HEAD:Main")
    (clone / "f.txt").write_text("local edit")
    st = update.status()
    assert st["dirty"] and st["behind"] == 1
    assert update.pull(st) is False
    assert (clone / "f.txt").read_text() == "local edit"


def test_repo_ok_rejects_non_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "REPO_DIR", tmp_path)
    assert "not a git checkout" in update.repo_ok()
