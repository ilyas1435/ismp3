"""ffprobe wrapper: duration, streams, tags. No external Python deps."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ToolMissingError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: str = ""
    duration: float = 0.0
    size: int = 0
    format_name: str = ""
    has_audio: bool = False
    has_video: bool = False
    bit_rate: int = 0
    sample_rate: int = 0
    channels: int = 0
    tags: dict[str, str] = field(default_factory=dict)
    audio_stream: dict[str, Any] = field(default_factory=dict)
    video_stream: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "duration": self.duration,
            "size": self.size,
            "format_name": self.format_name,
            "has_audio": self.has_audio,
            "has_video": self.has_video,
            "bit_rate": self.bit_rate,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "tags": self.tags,
        }


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise ToolMissingError(
            "ffmpeg غير مثبّت. على Termux: pkg install ffmpeg — على Replit: أضفه في replit.nix"
        )
    return path


def ffprobe_path() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise ToolMissingError(
            "ffprobe غير مثبّت (يأتي مع ffmpeg). على Termux: pkg install ffmpeg"
        )
    return path


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _loader_hint(detail: str) -> str:
    """رسالة مفهومة لتعارض مكتبات Nix على Replit بدل خطأ GLIBC الغامض."""
    if "GLIBC_" in detail or "libc.so" in detail or "libglib" in detail:
        return (
            "تعارض مكتبات Nix/GLIBC في بيئة Replit. تأكد أن .replit يحتوي "
            '[nix] channel = "stable-24_05" ثم أعد تحميل البيئة: '
            "اكتب exit في Shell وافتحه من جديد، أو Stop ثم Run."
        )
    return ""


def tool_issue(name: str) -> str:
    """يعيد رسالة المشكلة إذا كان ffmpeg/ffprobe لا يعمل، أو نصاً فارغاً."""
    path = shutil.which(name)
    if not path:
        return f"{name} غير مثبّت (يأتي مع ffmpeg)."
    try:
        result = _run([path, "-version"], timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{name} لا يعمل: {exc}"
    if result.returncode == 0:
        return ""
    detail = (result.stderr or result.stdout or "").strip()
    return _loader_hint(detail) or (detail.splitlines() or [f"{name} لا يعمل"])[-1]


def probe(path: str | Path) -> MediaInfo:
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"الملف غير موجود: {src}")
    result = _run(
        [
            ffprobe_path(),
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-of", "json",
            str(src),
        ]
    )
    if result.returncode != 0:
        raw = (result.stderr or "").strip()
        hint = _loader_hint(raw)
        if hint:
            raise ValueError(f"تعذّر قراءة الملف: {hint}")
        detail = raw.splitlines()
        raise ValueError(f"تعذّر قراءة الملف: {detail[-1] if detail else 'صيغة غير معروفة'}")

    data = json.loads(result.stdout or "{}")
    fmt = data.get("format") or {}
    streams: list[dict[str, Any]] = data.get("streams") or []
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}

    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        {},
    )
    cover = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and (s.get("disposition") or {}).get("attached_pic")
        ),
        {},
    )

    info = MediaInfo(
        path=str(src),
        duration=_as_float(fmt.get("duration") or audio.get("duration")),
        size=_as_int(fmt.get("size")) or src.stat().st_size,
        format_name=str(fmt.get("format_name") or ""),
        has_audio=bool(audio),
        has_video=bool(video),
        bit_rate=_as_int(fmt.get("bit_rate")),
        sample_rate=_as_int(audio.get("sample_rate")),
        channels=_as_int(audio.get("channels")),
        tags=tags,
        audio_stream=audio,
        video_stream=video or cover,
    )
    for key in ("title", "artist", "album", "track", "date", "genre", "comment"):
        stream_value = (audio.get("tags") or {}).get(key)
        if stream_value and key not in info.tags:
            info.tags[key] = str(stream_value)
    return info


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def ffmpeg_version() -> str:
    try:
        out = _run([ffmpeg_path(), "-version"], timeout=15).stdout
        return (out.splitlines() or ["ffmpeg"])[0]
    except (ToolMissingError, OSError, subprocess.SubprocessError):
        return ""


def has_encoder(name: str = "libmp3lame") -> bool:
    try:
        out = _run([ffmpeg_path(), "-hide_banner", "-encoders"], timeout=20).stdout
    except (ToolMissingError, OSError, subprocess.SubprocessError):
        return False
    return name in out


def has_video_stream(path: str | Path) -> bool:
    try:
        return probe(path).video_stream.get("codec_type") == "video"
    except (OSError, ValueError, ToolMissingError):
        return False
