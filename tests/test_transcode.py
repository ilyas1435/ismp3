from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ismp3.config import Settings
from ismp3.jobs import JobManager
from ismp3.probe import probe
from ismp3.transcode import (
    atempo_chain,
    auto_sample_rate,
    build_args,
    metadata_from_ytdlp,
    validate,
    vbr_quality,
)

FFMPEG = shutil.which("ffmpeg")


def make_wav(path: Path, seconds: float = 1.5) -> Path:
    subprocess.run(
        [
            FFMPEG or "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-ac", "1", "-ar", "44100", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg غير مثبّت")
def test_transcode_file_to_24k_mono(tmp_path: Path, isolated: Path) -> None:
    wav = make_wav(tmp_path / "sample.wav")
    settings = Settings(output_dir=str(tmp_path / "out"), bitrate=24, channels=1)

    manager = JobManager(settings, start_workers=False)
    try:
        job = manager.submit([str(wav)], settings=settings)[0]
        final = manager.run_sync(job)
    finally:
        manager.stop()

    assert final.status == "done", final.error
    assert final.outputs, final.error
    output = Path(final.outputs[0])
    assert output.is_file()
    assert output.suffix == ".mp3"
    assert output.stat().st_size > 0

    info = probe(output)
    assert info.has_audio
    assert info.sample_rate == 24000
    assert info.channels == 1
    assert info.tags.get("title")
    assert 1.0 < info.duration < 2.5


def test_auto_sample_rate_and_validate() -> None:
    assert auto_sample_rate(8) == 8000
    assert auto_sample_rate(16) == 16000
    assert auto_sample_rate(24) == 24000
    assert auto_sample_rate(32) == 44100
    assert validate(24, 0) == 24000
    assert validate(24, 24000) == 24000
    with pytest.raises(ValueError):
        validate(24, 44100)
    with pytest.raises(ValueError):
        validate(320, 24000)


def test_atempo_chain() -> None:
    assert atempo_chain(1.0) == ""
    assert atempo_chain(1.5) == "atempo=1.5000"
    assert "atempo=2.0" in atempo_chain(4.0)
    assert "atempo=0.5" in atempo_chain(0.25)


def test_vbr_quality() -> None:
    assert vbr_quality(16) == 9
    assert vbr_quality(64) == 6
    assert vbr_quality(320) == 1


def test_build_args_fast_encoder_flags(tmp_path: Path) -> None:
    settings = Settings(output_dir=str(tmp_path), bitrate=24, channels=1)
    args = build_args(tmp_path / "in.wav", tmp_path / "out.mp3", settings)
    assert args[args.index("-compression_level") + 1] == "5"
    assert args[args.index("-threads") + 1] == "0"


def test_metadata_from_ytdlp() -> None:
    meta = metadata_from_ytdlp(
        {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "release_date": "2024-05-01",
            "track_number": 4,
            "webpage_url": "https://example.test/v",
        }
    )
    assert meta["title"] == "Song"
    assert meta["artist"] == "Artist"
    assert meta["album"] == "Album"
    assert meta["date"] == "2024-05-01"
    assert meta["track"] == "4"
    assert meta["comment"] == "https://example.test/v"
