"""yt-dlp wrapper: inspect URLs, download the best audio, report errors in Arabic."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any

from .config import Settings
from .util import CancelToken

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
JUNK_EXTS = {
    ".part", ".ytdl", ".json", ".description", ".vtt", ".srt", ".ass",
    ".lrc", ".txt", ".temp", ".tmp",
}


class DownloadCancelled(RuntimeError):
    pass


class AlreadyDownloaded(RuntimeError):
    def __init__(self, item_id: str) -> None:
        super().__init__(item_id)
        self.item_id = item_id


class FetchError(RuntimeError):
    pass


def is_youtube(url: str) -> bool:
    value = str(url or "").lower()
    return any(host in value for host in ("youtube.com", "youtu.be", "youtube-nocookie.com"))


def _format_selector(settings: Settings, url: str) -> str:
    if not is_youtube(url):
        return "bestaudio/best"
    language = (settings.audio_lang or "original").strip()
    if language and language != "original":
        return f"bestaudio[language={language}]/bestaudio/best"
    # language_preference > 0 marks the original track; dubbed tracks get -1.
    return "bestaudio[language_preference>0]/bestaudio/best"


def _cookies(settings: Settings) -> str | None:
    path = settings.cookies_path
    if settings.cookies_file and (path is None or not path.is_file()):
        raise FetchError(f"ملف الكوكيز غير موجود: {settings.cookies_file}")
    return str(path) if path else None


def _common_opts(settings: Settings, on_progress: Callable[..., Any] | None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": settings.retries,
        "fragment_retries": settings.retries,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": settings.fragment_jobs,
        "continuedl": True,
        "overwrites": False,
        "ignoreerrors": False,
        "no_color": True,
    }
    cookies = _cookies(settings)
    if cookies:
        opts["cookiefile"] = cookies
    if settings.proxy:
        opts["proxy"] = settings.proxy
    if settings.rate_limit:
        opts["ratelimit"] = settings.rate_limit
    if settings.client and settings.client not in {"auto", "direct"}:
        opts["extractor_args"] = {"youtube": {"player_client": [settings.client]}}
    if on_progress:
        def hook(data: dict[str, Any]) -> None:
            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                done = data.get("downloaded_bytes") or 0
                fraction = (done / total) if total else 0.0
                on_progress(fraction, data.get("speed"), data.get("eta"))
            elif status == "finished":
                on_progress(1.0, data.get("speed"), 0)

        opts["progress_hooks"] = [hook]
    return opts


def inspect(url: str, settings: Settings) -> dict[str, Any]:
    """Return a compact description of a URL: video or playlist + entries."""
    from yt_dlp import YoutubeDL

    opts = _common_opts(settings, None)
    opts.update(
        {
            "skip_download": True,
            "extract_flat": "in_playlist",
            "noplaylist": not settings.playlist,
            "ignoreerrors": True,
            "writethumbnail": False,
        }
    )
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise FetchError("تعذّر قراءة الرابط (قد يكون خاصاً أو محجوباً).")
    if info.get("_type") == "playlist":
        entries: list[dict[str, Any]] = []
        for position, entry in enumerate(info.get("entries") or [], start=1):
            if not entry:
                continue
            entry_url = _entry_url(entry)
            if not entry_url:
                continue
            entries.append(
                {
                    "index": position,
                    "id": str(entry.get("id") or ""),
                    "title": str(entry.get("title") or entry.get("id") or "بدون عنوان"),
                    "url": entry_url,
                    "duration": entry.get("duration") or 0,
                    "uploader": entry.get("uploader") or entry.get("channel") or "",
                }
            )
        return {
            "kind": "playlist",
            "id": str(info.get("id") or ""),
            "title": str(info.get("title") or info.get("id") or "قائمة"),
            "uploader": str(info.get("uploader") or info.get("channel") or ""),
            "count": len(entries),
            "entries": entries,
        }

    return {
        "kind": "video",
        "id": str(info.get("id") or ""),
        "title": str(info.get("title") or info.get("id") or "بدون عنوان"),
        "uploader": str(info.get("uploader") or info.get("channel") or ""),
        "duration": info.get("duration") or 0,
        "webpage_url": str(info.get("webpage_url") or url),
        "extractor": str(info.get("extractor_key") or info.get("extractor") or ""),
        "thumbnail": str(info.get("thumbnail") or ""),
        "count": 1,
    }


def _entry_url(entry: dict[str, Any]) -> str:
    candidate = entry.get("webpage_url") or entry.get("url")
    if candidate and str(candidate).startswith(("http://", "https://")):
        return str(candidate)
    item_id = entry.get("id")
    if not item_id:
        return ""
    ie_key = str(entry.get("ie_key") or entry.get("extractor_key") or "").lower()
    if "youtube" in ie_key:
        return f"https://www.youtube.com/watch?v={item_id}"
    return ""


def download_one(
    url: str,
    workdir: str | Path,
    settings: Settings,
    token: CancelToken | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Download the best audio stream for one URL into *workdir*.

    Returns a dict with ``info``, ``path`` and optional ``thumbnail``.
    """
    from yt_dlp import YoutubeDL

    folder = Path(workdir)
    folder.mkdir(parents=True, exist_ok=True)

    def progress(fraction: Any, speed: Any, _eta: Any = None) -> None:
        if token and token.is_set():
            raise DownloadCancelled("أُلغي التنزيل")
        if on_progress:
            speed_text = f"{float(speed) / 1024:.0f} KB/s" if speed else ""
            on_progress(float(fraction or 0), speed_text)

    opts = _common_opts(settings, progress)
    opts.update(
        {
            "format": _format_selector(settings, url),
            "paths": {"home": str(folder), "temp": str(folder)},
            "outtmpl": {"default": "%(id)s.%(ext)s"},
            "writethumbnail": bool(settings.cover),
        }
    )
    if not force:
        # yt-dlp's own download archive is intentionally not used: the JSON
        # library is the single source of truth, so deleting a job really
        # allows it to be downloaded again.
        opts.setdefault("overwrites", False)

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadCancelled:
        raise
    except Exception as exc:  # yt-dlp raises DownloadError, but be defensive
        if token and token.is_set():
            raise DownloadCancelled("أُلغي التنزيل") from exc
        raise FetchError(friendly_error(exc)) from exc
    if not info:
        raise FetchError("لم يُرجع yt-dlp أي معلومات لهذا الرابط.")
    if info.get("_type") == "playlist":
        raise FetchError("هذا رابط قائمة؛ استخدم مسار القائمة.")

    item_id = str(info.get("id") or "")
    path = _find_media(folder, item_id)
    if not path:
        if not force:
            raise AlreadyDownloaded(item_id)
        raise FetchError("اكتمل التنزيل لكن لم يُعثر على الملف الناتج.")

    thumbnail = _find_thumbnail(folder, item_id)
    return {"info": info, "path": path, "thumbnail": thumbnail}


def _find_media(folder: Path, item_id: str) -> Path | None:
    candidates: list[Path] = []
    for path in folder.iterdir():
        if not path.is_file() or not item_id:
            continue
        if path.stem != item_id and not path.name.startswith(f"{item_id}."):
            continue
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTS or suffix in JUNK_EXTS or path.name.endswith(".part"):
            continue
        candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0]


def _find_thumbnail(folder: Path, item_id: str) -> Path | None:
    for path in folder.iterdir():
        if path.is_file() and path.stem == item_id and path.suffix.lower() in IMAGE_EXTS:
            return path
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            return path
    return None


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    hints = [
        ("sign in to confirm", "يوتيوب يطلب تسجيل دخول — أضف cookies.txt من متصفحك ثم أعد المحاولة."),
        ("confirm you're not a bot", "يوتيوب يشك في الطلب — استخدم cookies.txt (خصوصاً على Replit)."),
        ("age-restricted", "المحتوى مقيّد بالعمر — يحتاج cookies من حساب مسجّل."),
        ("private video", "الفيديو خاص ولا يمكن الوصول إليه."),
        ("video unavailable", "الفيديو غير متاح (محذوف أو مقيّد جغرافياً)."),
        ("not available in your country", "المحتوى محجوب جغرافياً — جرّب proxy."),
        ("http error 429", "طلبات كثيرة (429) — انتظر قليلاً أو قلّل التزامن."),
        ("http error 403", "رُفض الطلب (403) — حدّث yt-dlp أو أضف cookies."),
        ("unsupported url", "الرابط غير مدعوم."),
        ("timed out", "انتهت المهلة — تحقق من الشبكة وأعد المحاولة."),
        ("unable to download", "تعذّر التنزيل — حدّث yt-dlp أولاً: ismp3 update"),
    ]
    for needle, hint in hints:
        if needle in low:
            return f"{hint}\n({text.splitlines()[0] if text else 'خطأ'})"
    return text or "خطأ غير معروف من yt-dlp"
