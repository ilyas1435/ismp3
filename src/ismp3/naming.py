"""Safe, human-friendly file names. Pure functions, easy to test."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from .config import Settings

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WHITESPACE = re.compile(r"\s+")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_COMPONENT_BYTES = 180


def _truncate_bytes(text: str, limit: int = _MAX_COMPONENT_BYTES) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    cut = encoded[:limit]
    while cut:
        try:
            return cut.decode("utf-8").rstrip(" .-")
        except UnicodeDecodeError:
            cut = cut[:-1]
    return "audio"


def sanitize(text: Any, fallback: str = "audio") -> str:
    """Turn arbitrary metadata into one safe path component (keeps Unicode)."""
    value = unicodedata.normalize("NFC", str(text or "")).strip()
    value = _INVALID.sub("-", value)
    value = value.replace("\\", "-").replace("/", "-")
    value = _WHITESPACE.sub(" ", value).strip(" .-")
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C")
    value = value.strip(" .-")
    if not value:
        value = fallback
    if value.upper() in _WINDOWS_RESERVED:
        value = f"_{value}"
    return _truncate_bytes(value)


def first(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def parse_artist_title(stem: str) -> tuple[str, str]:
    """Best-effort split of ``Artist - Title`` used only when tags are absent."""
    text = str(stem or "").strip()
    for sep in (" - ", " – ", " — "):
        if sep in text:
            artist, title = text.split(sep, 1)
            if artist.strip() and title.strip():
                return artist.strip(), title.strip()
    return "", text


def display_title(info: dict[str, Any], fallback: str = "صوت") -> str:
    title = first(info.get("track"), info.get("title"), fallback)
    artist = first(info.get("artist"), info.get("creator"), info.get("uploader"))
    if artist and not title.lower().startswith(artist.lower()):
        return f"{artist} - {title}"
    return title


def _filename_stem(info: dict[str, Any], layout: str, index: int | None) -> str:
    title = sanitize(first(info.get("track"), info.get("title"), "صوت"))
    artist = sanitize(first(info.get("artist"), info.get("creator"), info.get("uploader"), ""), "")
    item_id = sanitize(first(info.get("id"), ""), "")

    prefix = ""
    if layout == "playlist" and index:
        prefix = f"{index:02d} - "
    elif layout == "album":
        track_no = first(info.get("track_number"), info.get("track"))
        digits = re.match(r"^(\d{1,3})", str(track_no or ""))
        if digits:
            prefix = f"{int(digits.group(1)):02d} - "

    if layout == "flat" and artist and not title.lower().startswith(artist.lower()):
        title = f"{artist} - {title}"

    suffix = f" [{item_id}]" if item_id and item_id.lower() not in title.lower() else ""
    stem = f"{prefix}{title}{suffix}"
    # Guard against a metadata explosion producing a 2 KB filename.
    return _truncate_bytes(stem, 150) or sanitize(title)


def build_path(
    output_dir: str | Path,
    info: dict[str, Any],
    settings: Settings,
    index: int | None = None,
) -> Path:
    """Build the final MP3 path for an item according to the configured layout."""
    base = Path(output_dir).expanduser()
    layout = settings.layout
    artist = sanitize(first(info.get("artist"), info.get("creator"), info.get("uploader"), "فنان غير معروف"), "فنان غير معروف")
    album = sanitize(first(info.get("album"), "ألبوم غير معروف"), "ألبوم غير معروف")
    playlist = sanitize(first(info.get("playlist_title"), info.get("playlist"), "قائمة"), "قائمة")

    parts: list[str] = []
    if layout in {"artist", "album"}:
        parts.append(artist)
    if layout == "album":
        parts.append(album)
    elif layout == "playlist":
        parts.append(playlist)

    stem = _filename_stem(info, layout, index)
    return base.joinpath(*parts, f"{stem}.mp3")


def all_media_suffixes() -> set[str]:
    from .util import MEDIA_EXTS

    return MEDIA_EXTS
