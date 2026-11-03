"""Tiny dedupe store. JSON, one file, no database."""

from __future__ import annotations

import hashlib
import threading
from typing import Any

from .config import seen_file
from .util import atomic_write_json, now_iso, read_json


def key_for(info: dict[str, Any], url: str = "") -> str:
    extractor = str(info.get("extractor_key") or info.get("extractor") or info.get("ie_key") or "")
    item_id = str(info.get("id") or "")
    if extractor and item_id:
        return f"{extractor.lower()}:{item_id}"
    if item_id:
        return f"id:{item_id}"
    return f"url:{hashlib.sha256(str(url).encode('utf-8')).hexdigest()[:24]}"


class Library:
    def __init__(self, path: str | None = None) -> None:
        from pathlib import Path

        self.path = Path(path) if path else seen_file()
        self._lock = threading.Lock()
        raw = read_json(self.path, {})
        self._data: dict[str, Any] = raw if isinstance(raw, dict) else {}

    def seen(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._data.get(key)
            return dict(value) if isinstance(value, dict) else None

    def remember(self, key: str, title: str = "", path: str = "") -> None:
        with self._lock:
            self._data[key] = {"at": now_iso(), "title": title, "path": path}
            self._flush()

    def forget(self, key: str) -> None:
        with self._lock:
            if self._data.pop(key, None) is not None:
                self._flush()

    def forget_path(self, path: str) -> None:
        with self._lock:
            changed = False
            for key, value in list(self._data.items()):
                if isinstance(value, dict) and value.get("path") == path:
                    self._data.pop(key, None)
                    changed = True
            if changed:
                self._flush()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._flush()

    def count(self) -> int:
        with self._lock:
            return len(self._data)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {key: dict(value) if isinstance(value, dict) else value
                    for key, value in self._data.items()}

    def _flush(self) -> None:
        try:
            atomic_write_json(self.path, self._data, mode=0o600)
        except OSError:
            pass
