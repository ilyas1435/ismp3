"""ffmpeg: everything → MP3, with full control over bitrate/sample-rate.

At 24 kbit/s MP3 is valid only in MPEG-2 (16/22.05/24 kHz) or MPEG-2.5
(8/11.025/12 kHz); 44.1 kHz would be silently bumped by ffmpeg, so we validate.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .naming import parse_artist_title
from .probe import MediaInfo, ffmpeg_path, probe
from .util import CancelToken, move_file, unique_path

ProgressFn = Callable[[float, str], None]

MPEG1_RATES = {32000, 44100, 48000}
MPEG2_RATES = {16000, 22050, 24000}
MPEG25_RATES = {8000, 11025, 12000}

BITRATES: dict[int, set[int]] = {
    **{rate: {32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320} for rate in MPEG1_RATES},
    **{rate: {8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160} for rate in MPEG2_RATES},
    **{rate: {8, 16, 24, 32, 40, 48, 56, 64} for rate in MPEG25_RATES},
}
VALID_RATES = sorted(BITRATES)
STANDARD_BITRATES = [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]

PRESETS: dict[str, dict[str, Any]] = {
    "tiny": {"bitrate": 16, "sample_rate": 16000, "channels": 1, "mode": "cbr"},
    "voice": {"bitrate": 24, "sample_rate": 24000, "channels": 1, "mode": "cbr"},
    "music": {"bitrate": 64, "sample_rate": 44100, "channels": 2, "mode": "cbr"},
    "hq": {"bitrate": 128, "sample_rate": 44100, "channels": 2, "mode": "cbr"},
}


class CancelledError(RuntimeError):
    pass


def auto_sample_rate(bitrate: int) -> int:
    if bitrate >= 32:
        return 44100
    if bitrate >= 20:
        return 24000
    if bitrate >= 12:
        return 16000
    return 8000


def validate(bitrate: int, sample_rate: int = 0) -> int:
    """Return a valid sample rate or raise a helpful Arabic error."""
    bitrate = int(bitrate)
    rate = int(sample_rate) or auto_sample_rate(bitrate)
    if rate not in BITRATES:
        raise ValueError(
            f"تردد {rate} Hz غير مدعوم في MP3. المتاح: {', '.join(map(str, VALID_RATES))}"
        )
    if bitrate not in BITRATES[rate]:
        allowed = sorted(BITRATES[rate])
        nearest = min(allowed, key=lambda value: abs(value - bitrate))
        raise ValueError(
            f"البتريت {bitrate}k غير صالح مع {rate} Hz في MP3. "
            f"القيم المتاحة: {', '.join(f'{v}k' for v in allowed)} "
            f"(الأقرب: {nearest}k) — أو اترك التردد auto."
        )
    return rate


def vbr_quality(bitrate: int) -> int:
    if bitrate <= 24:
        return 9
    if bitrate <= 32:
        return 8
    if bitrate <= 48:
        return 7
    if bitrate <= 64:
        return 6
    if bitrate <= 96:
        return 5
    if bitrate <= 128:
        return 4
    if bitrate <= 160:
        return 3
    if bitrate <= 192:
        return 2
    return 1


def atempo_chain(speed: float) -> str:
    """ffmpeg atempo accepts 0.5–2.0 per filter; chain for wider ranges."""
    speed = float(speed)
    if abs(speed - 1.0) < 0.001:
        return ""
    filters: list[str] = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)


def metadata_from_media(info: MediaInfo, source: str | Path) -> dict[str, str]:
    tags = info.tags or {}
    stem = Path(source).stem
    guess_artist, guess_title = parse_artist_title(stem)
    title = tags.get("title") or guess_title or stem
    artist = tags.get("artist") or tags.get("album_artist") or guess_artist
    meta: dict[str, str] = {"title": title}
    if artist:
        meta["artist"] = artist
    if tags.get("album"):
        meta["album"] = tags["album"]
    if tags.get("date"):
        meta["date"] = tags["date"][:10]
    if tags.get("track"):
        meta["track"] = tags["track"]
    if tags.get("genre"):
        meta["genre"] = tags["genre"]
    return meta


def metadata_from_ytdlp(info: dict[str, Any]) -> dict[str, str]:
    title = str(info.get("track") or info.get("title") or "صوت")
    artist = str(
        info.get("artist")
        or info.get("creator")
        or info.get("uploader")
        or info.get("channel")
        or ""
    )
    meta: dict[str, str] = {"title": title}
    if artist:
        meta["artist"] = artist
    if info.get("album"):
        meta["album"] = str(info["album"])
    date = str(info.get("release_date") or info.get("upload_date") or "")
    if date:
        meta["date"] = date[:4] if len(date) == 8 else date[:10]
    track = info.get("track_number") or info.get("playlist_index")
    if track:
        meta["track"] = str(track)
    if info.get("webpage_url"):
        meta["comment"] = str(info["webpage_url"])
    return meta


def build_args(
    source: str | Path,
    output: str | Path,
    settings: Settings,
    metadata: dict[str, str] | None = None,
    cover: str | Path | None = None,
    sample_rate: int | None = None,
) -> list[str]:
    rate = validate(settings.bitrate, settings.sample_rate if sample_rate is None else sample_rate)
    cmd: list[str] = [
        ffmpeg_path(), "-hide_banner", "-nostdin", "-y",
        "-i", str(source),
    ]
    if cover:
        cmd += ["-i", str(cover)]

    cmd += ["-map", "0:a:0"]
    if cover:
        cmd += [
            "-map", "1:v:0",
            "-frames:v", "1",
            "-c:v", "mjpeg",
            "-disposition:v", "attached_pic",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", "comment=Cover (front)",
        ]
    else:
        cmd += ["-vn"]
    cmd += ["-sn", "-dn", "-map_metadata", "0"]

    chain = atempo_chain(settings.speed)
    if chain:
        cmd += ["-filter:a", chain]
    cmd += ["-ar", str(rate), "-ac", str(1 if settings.channels == 1 else 2)]

    cmd += ["-c:a", "libmp3lame", "-compression_level", "0"]
    if settings.mode == "vbr":
        cmd += ["-q:a", str(vbr_quality(settings.bitrate))]
    elif settings.mode == "abr":
        cmd += ["-b:a", f"{settings.bitrate}k", "-abr", "1"]
    else:
        cmd += ["-b:a", f"{settings.bitrate}k"]

    cmd += ["-id3v2_version", "3", "-write_id3v1", "1"]
    for key, value in (metadata or {}).items():
        if value:
            cmd += ["-metadata", f"{key}={value}"]
    cmd += ["-progress", "pipe:1", "-nostats", "-loglevel", "warning", str(output)]
    return cmd


def extract_cover(
    source: str | Path,
    dest: str | Path,
    max_size: int = 160,
) -> Path | None:
    """Extract the first embedded picture and shrink it. Returns None if absent."""
    target = Path(dest)
    cmd = [
        ffmpeg_path(), "-hide_banner", "-nostdin", "-y",
        "-i", str(source),
        "-map", "0:v:0",
        "-frames:v", "1",
        "-vf", f"scale={max_size}:{max_size}:force_original_aspect_ratio=decrease",
        "-c:v", "mjpeg",
        "-q:v", "5",
        str(target),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        try:
            target.unlink()
        except OSError:
            pass
        return None
    return target


def transcode(
    source: str | Path,
    output: str | Path,
    settings: Settings,
    metadata: dict[str, str] | None = None,
    duration: float = 0.0,
    cover: str | Path | None = None,
    on_progress: ProgressFn | None = None,
    token: CancelToken | None = None,
) -> Path:
    src, dest = Path(source), Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = unique_path(dest)
    cmd = build_args(src, dest, settings, metadata, cover)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        errors="replace",
    )
    if token:
        token.attach(proc)

    err_tail: deque[str] = deque(maxlen=40)
    stderr_thread = threading.Thread(
        target=lambda: [err_tail.append(line.rstrip()) for line in (proc.stderr or [])],
        daemon=True,
    )
    stderr_thread.start()

    last_speed = ""
    cancelled = False
    try:
        for raw in proc.stdout or []:
            if token and token.is_set():
                cancelled = True
                try:
                    proc.terminate()
                except OSError:
                    pass
                break
            line = raw.strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"out_time_us", "out_time_ms"}:
                try:
                    seconds = int(value) / 1_000_000
                except ValueError:
                    continue
                if duration > 0 and on_progress:
                    on_progress(min(max(seconds / duration, 0.0), 1.0), last_speed)
            elif key == "speed":
                last_speed = value if value != "N/A" else ""
            elif key == "progress" and value == "end" and on_progress:
                on_progress(1.0, last_speed)
        code = proc.wait()
    finally:
        if token:
            token.detach()
        stderr_thread.join(timeout=2)

    if cancelled or (token and token.is_set()):
        try:
            dest.unlink()
        except OSError:
            pass
        raise CancelledError("أُلغي التحويل")

    if code != 0:
        try:
            dest.unlink()
        except OSError:
            pass
        tail = " | ".join(err_tail)
        raise RuntimeError(f"فشل ffmpeg (كود {code}): {tail or 'خطأ غير معروف'}")

    if not dest.is_file() or dest.stat().st_size == 0:
        raise RuntimeError("ffmpeg انتهى دون إنتاج ملف")
    return dest


def transcode_file(
    source: str | Path,
    dest: str | Path,
    settings: Settings,
    on_progress: ProgressFn | None = None,
    token: CancelToken | None = None,
) -> Path:
    """Convert one local media file to MP3, extracting tags and optional cover."""
    src = Path(source)
    info = probe(src)
    if not info.has_audio:
        raise ValueError(f"لا يوجد مسار صوتي في: {src.name}")
    meta = metadata_from_media(info, src)
    cover_path: Path | None = None
    keep_cover = False
    if settings.cover:
        cover_path = extract_cover(src, Path(dest).with_suffix(".cover.jpg"))
        keep_cover = False
    try:
        return transcode(
            src,
            dest,
            settings,
            metadata=meta,
            duration=info.duration,
            cover=cover_path,
            on_progress=on_progress,
            token=token,
        )
    finally:
        if cover_path and not keep_cover:
            try:
                cover_path.unlink()
            except OSError:
                pass


def preset_settings(settings: Settings, name: str) -> Settings:
    preset = PRESETS.get(name.lower())
    if not preset:
        return settings
    return settings.with_overrides(**preset)


def wait_for_file(path: str | Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).is_file() and Path(path).stat().st_size > 0:
            return True
        time.sleep(0.05)
    return False


def move_into_place(source: str | Path, target: str | Path) -> Path:
    return move_file(source, target)
