from __future__ import annotations

from ismp3.config import Settings
from ismp3.fetch import _format_selector, is_youtube

YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_youtube_detection() -> None:
    assert is_youtube(YOUTUBE)
    assert is_youtube("https://youtu.be/dQw4w9WgXcQ")
    assert not is_youtube("https://example.test/audio.mp3")


def test_low_bitrate_downloads_smallest_stream() -> None:
    settings = Settings(bitrate=24)
    assert _format_selector(settings, YOUTUBE).startswith("worstaudio")


def test_high_bitrate_keeps_best_stream() -> None:
    settings = Settings(bitrate=128)
    selector = _format_selector(settings, YOUTUBE)
    assert selector.startswith("bestaudio")
    assert "worstaudio" not in selector


def test_language_filter_is_preserved() -> None:
    settings = Settings(bitrate=24, audio_lang="ar")
    selector = _format_selector(settings, YOUTUBE)
    assert "language=ar" in selector
    assert selector.startswith("worstaudio")


def test_non_youtube_still_uses_bestaudio() -> None:
    settings = Settings(bitrate=24)
    assert _format_selector(settings, "https://example.test/song") == "bestaudio/best"
