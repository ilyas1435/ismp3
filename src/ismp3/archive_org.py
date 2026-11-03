"""Optional Internet Archive upload (public items). Disabled unless configured."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .config import ia_credentials_file
from .util import atomic_write_json, new_id, read_json

ProgressFn = Callable[[str], None]


class ArchiveError(RuntimeError):
    pass


def _safe_identifier(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip()).strip("-._").lower()
    return text[:100]


def load_credentials() -> dict[str, str] | None:
    access = os.environ.get("IA_ACCESS_KEY", "").strip()
    secret = os.environ.get("IA_SECRET_KEY", "").strip()
    if access and secret:
        return {"access": access, "secret": secret, "source": "env"}
    data = read_json(ia_credentials_file(), None)
    if isinstance(data, dict) and data.get("access") and data.get("secret"):
        return {
            "access": str(data["access"]),
            "secret": str(data["secret"]),
            "source": "file",
        }
    return None


def save_credentials(access: str, secret: str) -> None:
    access, secret = str(access or "").strip(), str(secret or "").strip()
    if not access or not secret:
        raise ArchiveError("access و secret مطلوبان")
    atomic_write_json(
        ia_credentials_file(),
        {"access": access, "secret": secret, "saved_at": datetime.now(UTC).isoformat()},
        mode=0o600,
    )


def clear_credentials() -> None:
    try:
        ia_credentials_file().unlink()
    except OSError:
        pass


def credentials_status() -> dict[str, Any]:
    credentials = load_credentials()
    return {
        "configured": bool(credentials),
        "source": credentials["source"] if credentials else "",
        "file": str(ia_credentials_file()),
    }


def upload(
    files: list[str | Path],
    identifier: str = "",
    title: str = "",
    creator: str = "ismp3",
    on_progress: ProgressFn | None = None,
) -> dict[str, str]:
    try:
        import internetarchive as ia
    except ImportError as exc:
        raise ArchiveError(
            "مكتبة internetarchive غير مثبّتة. ثبّتها: pip install 'ismp3[ia]'"
        ) from exc

    credentials = load_credentials()
    if not credentials:
        raise ArchiveError("بيانات Archive.org غير مضبوطة (access/secret).")

    paths = [Path(item) for item in files if Path(item).is_file()]
    if not paths:
        raise ArchiveError("لا توجد ملفات صالحة للرفع.")

    item_id = _safe_identifier(identifier)
    if not item_id:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        item_id = f"ismp3-{stamp}-{new_id()[:6]}"
    display_title = title or paths[0].stem

    metadata = {
        "mediatype": "audio",
        "collection": "opensource_audio",
        "title": display_title,
        "creator": creator,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    if on_progress:
        on_progress(f"جارٍ رفع {len(paths)} ملف إلى {item_id}…")
    try:
        ia.upload(
            item_id,
            [str(path) for path in paths],
            metadata=metadata,
            access_key=credentials["access"],
            secret_key=credentials["secret"],
            retries=3,
            retries_sleep=5,
            verbose=False,
        )
    except Exception as exc:
        raise ArchiveError(f"فشل الرفع إلى Archive.org: {exc}") from exc

    url = f"https://archive.org/details/{item_id}"
    if on_progress:
        on_progress(f"تم الرفع: {url}")
    return {"identifier": item_id, "url": url}
