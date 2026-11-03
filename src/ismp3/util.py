"""Small shared helpers. No third-party imports."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Iterable
from typing import Any

MEDIA_EXTS = {
    # audio
    ".mp3", ".m4a", ".aac", ".opus", ".ogg", ".oga", ".wav", ".flac",
    ".wma", ".aiff", ".aif", ".amr", ".ape", ".mka", ".weba",
    # video
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".wmv", ".m4v",
    ".3gp", ".mpeg", ".mpg", ".ts", ".mts", ".m2ts", ".ogv", ".vob",
}

ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def human_size(num: float | int | None) -> str:
    n = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def estimate_size(duration: float | int | None, bitrate: int) -> int:
    """Estimated MP3 bytes for a duration at a constant bitrate (kbit/s)."""
    if not duration or duration <= 0:
        return 0
    return int(float(duration) * float(bitrate) * 1000 / 8)


def format_duration(seconds: float | int | None) -> str:
    total = max(0, int(seconds or 0))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def atomic_write_text(path: str | Path, text: str, mode: int = 0o644) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def atomic_write_json(path: str | Path, data: Any, mode: int = 0o644) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2), mode=mode)


def read_json(path: str | Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def is_url(value: str) -> bool:
    return bool(re.match(r"^https?://", str(value or "").strip(), re.I))


def is_archive_name(value: str) -> bool:
    return Path(str(value)).suffix.lower() in ARCHIVE_EXTS


def parse_pick(spec: str | None) -> list[int] | None:
    """Parse a 1-based selection such as ``1,3,5-9``. None/empty means all."""
    if spec is None:
        return None
    text = str(spec).strip()
    if not text or text in {"*", "all", "الكل"}:
        return None
    picked: set[int] = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            left, right = chunk.split("-", 1)
            start, end = int(left), int(right)
            if start < 1 or end < start:
                raise ValueError(f"نطاق غير صالح: {chunk}")
            picked.update(range(start, end + 1))
        else:
            value = int(chunk)
            if value < 1:
                raise ValueError(f"رقم غير صالح: {chunk}")
            picked.add(value)
    return sorted(picked) or None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def within_roots(path: str | Path, roots: Iterable[str | Path]) -> bool:
    """True when *path* resolves inside one of the allowed roots."""
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            base = Path(root).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        try:
            target.relative_to(base)
            return True
        except ValueError:
            if target == base:
                return True
    return False


def unique_path(path: str | Path) -> Path:
    """Return *path*, or ``name (2).ext`` if it already exists."""
    p = Path(path)
    if not p.exists():
        return p
    parent, stem, suffix = p.parent, p.stem, p.suffix
    for i in range(2, 10_000):
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return parent / f"{stem} ({new_id()}){suffix}"


def move_file(src: str | Path, dest: str | Path) -> Path:
    """Cross-device-safe move (Android FUSE often cannot rename across mounts)."""
    src_p, dest_p = Path(src), Path(dest)
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src_p, dest_p)
    except OSError:
        shutil.copy2(src_p, dest_p)
        try:
            src_p.unlink()
        except OSError:
            pass
    return dest_p


def zip_files(files: Iterable[str | Path], dest: str | Path) -> Path:
    """Store files without compression (MP3 is already compressed)."""
    dest_p = Path(dest)
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    with zipfile.ZipFile(dest_p, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for item in files:
            p = Path(item)
            if not p.is_file():
                continue
            arc = p.name
            if arc in used:
                arc = f"{p.parent.name}/{p.name}" if p.parent.name else arc
            if arc in used:
                arc = f"{p.stem} ({new_id()}){p.suffix}"
            used.add(arc)
            zf.write(p, arcname=arc)
    return dest_p


def list_media(root: str | Path, recursive: bool = False) -> list[Path]:
    base = Path(root)
    it = base.rglob("*") if recursive else base.iterdir()
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in MEDIA_EXTS)


class CancelToken:
    """Thread-safe cancellation shared by yt-dlp hooks and ffmpeg processes."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._proc: Any = None
        self._lock = threading.Lock()

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        self._event.set()
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def attach(self, proc: Any) -> None:
        with self._lock:
            self._proc = proc
        if self._event.is_set():
            self.set()

    def detach(self) -> None:
        with self._lock:
            self._proc = None
