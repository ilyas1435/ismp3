from __future__ import annotations

from pathlib import Path

from ismp3.config import Settings
from ismp3.naming import (
    build_path,
    display_title,
    parse_artist_title,
    sanitize,
)


def test_sanitize_removes_path_characters() -> None:
    dirty = 'a/b\\c:d*e?f"g<h>i|j\x00'
    clean = sanitize(dirty)
    assert not any(char in clean for char in '/\\:*?"<>|\x00')
    assert clean


def test_sanitize_windows_reserved() -> None:
    assert sanitize("CON") == "_CON"
    assert sanitize("lpt1") == "_lpt1"


def test_sanitize_is_byte_limited() -> None:
    value = sanitize("ع" * 500)
    assert len(value.encode("utf-8")) <= 180
    assert value


def test_sanitize_empty_fallback() -> None:
    assert sanitize("   ...  ") == "audio"
    assert sanitize("", fallback="صوت") == "صوت"


def test_parse_artist_title() -> None:
    assert parse_artist_title("Artist - Song") == ("Artist", "Song")
    assert parse_artist_title("Artist – Song") == ("Artist", "Song")
    assert parse_artist_title("No separator") == ("", "No separator")


def test_display_title_avoids_duplicate_artist() -> None:
    assert display_title({"artist": "A", "title": "B"}) == "A - B"
    assert display_title({"artist": "A", "title": "A - B"}) == "A - B"


def test_build_path_flat_and_album() -> None:
    settings = Settings(output_dir="/tmp/out", layout="flat")
    info = {"id": "abc", "title": "Song", "artist": "Artist"}
    flat = build_path("/tmp/out", info, settings)
    assert flat == Path("/tmp/out/Artist - Song [abc].mp3")

    album_settings = Settings(output_dir="/tmp/out", layout="album")
    album_info = {
        "id": "abc",
        "title": "Song",
        "artist": "Artist",
        "album": "Record",
        "track_number": 3,
    }
    album = build_path("/tmp/out", album_info, album_settings)
    assert album == Path("/tmp/out/Artist/Record/03 - Song [abc].mp3")


def test_build_path_playlist_has_index() -> None:
    settings = Settings(output_dir="/tmp/out", layout="playlist")
    info = {"id": "x", "title": "Track", "playlist_title": "Mix"}
    path = build_path("/tmp/out", info, settings, index=4)
    assert path == Path("/tmp/out/Mix/04 - Track [x].mp3")
