"""Shared pytest fixtures: keep every test away from real user data."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME/XDG/TMP at a throwaway tree."""
    home = tmp_path / "home"
    config = tmp_path / "config"
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    for folder in (home, config, data, cache):
        folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("TMPDIR", str(cache))
    monkeypatch.setenv("ISMP3_CONFIG", str(config / "ismp3" / "config.toml"))
    monkeypatch.setattr(tempfile, "tempdir", str(cache))
    return tmp_path
