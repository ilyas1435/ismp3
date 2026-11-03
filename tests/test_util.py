from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from ismp3.util import (
    estimate_size,
    format_duration,
    human_size,
    parse_pick,
    unique_path,
    within_roots,
    zip_files,
)


def test_human_size() -> None:
    assert human_size(0) == "0 B"
    assert human_size(1024) == "1.0 KB"
    assert human_size(1024 * 1024) == "1.0 MB"


def test_estimate_size() -> None:
    assert estimate_size(0, 24) == 0
    assert estimate_size(3600, 24) == 10_800_000
    assert estimate_size(None, 24) == 0


def test_format_duration() -> None:
    assert format_duration(0) == "0:00"
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"


def test_parse_pick() -> None:
    assert parse_pick(None) is None
    assert parse_pick("") is None
    assert parse_pick("الكل") is None
    assert parse_pick("1,3,5-7") == [1, 3, 5, 6, 7]
    assert parse_pick("2") == [2]
    with pytest.raises(ValueError):
        parse_pick("0")
    with pytest.raises(ValueError):
        parse_pick("5-3")


def test_within_roots(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "a" / "b.txt"
    inside.parent.mkdir()
    inside.write_text("x")
    assert within_roots(inside, [root])
    assert within_roots(root, [root])
    assert not within_roots(tmp_path / "outside.txt", [root])


def test_unique_path(tmp_path: Path) -> None:
    first = tmp_path / "song.mp3"
    first.write_bytes(b"x")
    second = unique_path(first)
    assert second.name == "song (2).mp3"
    second.write_bytes(b"x")
    assert unique_path(first).name == "song (3).mp3"


def test_zip_files(tmp_path: Path) -> None:
    one = tmp_path / "one.mp3"
    two = tmp_path / "two.mp3"
    one.write_bytes(b"a" * 10)
    two.write_bytes(b"b" * 10)
    dest = tmp_path / "bundle.zip"
    zip_files([one, two], dest)
    with zipfile.ZipFile(dest) as archive:
        assert sorted(archive.namelist()) == ["one.mp3", "two.mp3"]
