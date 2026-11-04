from __future__ import annotations

import subprocess
from pathlib import Path

from ismp3 import updater


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def configure(repo: Path) -> None:
    git(repo, "config", "user.name", "ismp3 test")
    git(repo, "config", "user.email", "test@example.invalid")


def test_pull_updates_from_origin(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))

    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "--initial-branch=main")
    configure(seed)
    (seed / "version.txt").write_text("v1\n", encoding="utf-8")
    git(seed, "add", "version.txt")
    git(seed, "commit", "-m", "v1")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", "main")

    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(remote), str(clone))

    other = tmp_path / "other"
    git(tmp_path, "clone", str(remote), str(other))
    configure(other)
    (other / "version.txt").write_text("v2\n", encoding="utf-8")
    git(other, "commit", "-am", "v2")
    git(other, "push", "origin", "main")

    ok, updated, message = updater.pull(clone)
    assert ok is True, message
    assert updated is True
    assert (clone / "version.txt").read_text(encoding="utf-8") == "v2\n"

    ok, updated, message = updater.pull(clone)
    assert ok is True, message
    assert updated is False


def test_pull_rejects_non_git_folder(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    ok, updated, message = updater.pull(plain)
    assert ok is False
    assert updated is False
    assert "Git" in message
