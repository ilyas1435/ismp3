"""In-process job queue, playlist handling, cancellation and persistence.

Deliberately no database: one ``jobs.json`` snapshot plus an in-memory queue.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import fetch, naming, transcode
from .config import Settings, data_dir, jobs_file, work_root
from .library import Library, key_for
from .probe import probe
from .util import (
    ARCHIVE_EXTS,
    CancelToken,
    atomic_write_json,
    human_size,
    is_url,
    list_media,
    move_file,
    new_id,
    now_iso,
    read_json,
    unique_path,
    zip_files,
)

ACTIVE_STATUSES = {"queued", "running", "uploading"}

ARABIC_LABELS = {
    "queued": "في الانتظار",
    "running": "جارٍ التنفيذ",
    "downloading": "جارٍ التنزيل",
    "converting": "جارٍ التحويل",
    "done": "تم",
    "error": "خطأ",
    "cancelled": "أُلغي",
    "interrupted": "توقف — أعد المحاولة",
    "uploading": "جارٍ الرفع",
}


@dataclass
class Job:
    id: str
    kind: str = "url"
    status: str = "queued"
    label: str = "في الانتظار"
    progress: float = 0.0
    speed: str = ""
    sources: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    title: str = ""
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    size: int = 0
    duration: float = 0.0
    count: int = 0
    done_count: int = 0
    bitrate: int = 24
    settings: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    seen_key: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = now_iso()
        if not self.label:
            self.label = ARABIC_LABELS.get(self.status, self.status)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def public(self) -> dict[str, Any]:
        data = self.to_dict()
        data["size_text"] = human_size(self.size)
        data["progress"] = round(float(self.progress or 0), 4)
        return data


class JobManager:
    def __init__(
        self,
        settings: Settings,
        workers: int | None = None,
        start_workers: bool = True,
    ) -> None:
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.queue: queue.Queue[str | None] = queue.Queue()
        self.tokens: dict[str, CancelToken] = {}
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._last_persist: dict[str, float] = {}
        self._last_publish: dict[str, float] = {}
        self.library = Library()
        self.on_event: Callable[[dict[str, Any]], None] | None = None
        self._load()
        count = int(workers or settings.jobs or 2)
        self._threads = [
            threading.Thread(target=self._worker, name=f"ismp3-worker-{i}", daemon=True)
            for i in range(max(1, count))
        ]
        if start_workers:
            for thread in self._threads:
                thread.start()

    # ── persistence ────────────────────────────────────────────────────────

    def _load(self) -> None:
        raw = read_json(jobs_file(), [])
        if not isinstance(raw, list):
            return
        for item in raw[-500:]:
            try:
                job = Job.from_dict(item)
            except (TypeError, ValueError):
                continue
            if job.status in ACTIVE_STATUSES:
                job.status = "interrupted"
                job.label = ARABIC_LABELS["interrupted"]
                job.speed = ""
            self.jobs[job.id] = job
            self.order.append(job.id)

    def _persist(self, job: Job, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_persist.get(job.id, 0) < 1.0:
            return
        self._last_persist[job.id] = now
        with self._lock:
            data = [self.jobs[jid].to_dict() for jid in self.order if jid in self.jobs]
            try:
                data_dir().mkdir(parents=True, exist_ok=True)
                atomic_write_json(jobs_file(), data, mode=0o600)
            except OSError:
                pass

    def _publish(self, job: Job, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_publish.get(job.id, 0) < 0.20:
            return
        self._last_publish[job.id] = now
        event = {"type": "job", "job": job.public()}
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass
        dead: list[queue.Queue[dict[str, Any]]] = []
        for subscriber in list(self._subscribers):
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                dead.append(subscriber)
        for subscriber in dead:
            self._subscribers.discard(subscriber)

    def _update(self, job: Job, force: bool = True, **values: Any) -> None:
        for key, value in values.items():
            setattr(job, key, value)
        self._persist(job, force=force)
        self._publish(job, force=force)

    def _progress(
        self,
        job: Job,
        progress: float,
        label: str | None = None,
        speed: str | None = None,
    ) -> None:
        job.progress = round(max(0.0, min(1.0, float(progress))), 4)
        if label is not None:
            job.label = label
        job.speed = speed or ""
        self._persist(job)
        self._publish(job)

    # ── public API ─────────────────────────────────────────────────────────

    def submit(
        self,
        sources: list[str],
        settings: Settings | None = None,
        pick: list[int] | None = None,
        bundle: bool = False,
        force: bool = False,
        title: str = "",
    ) -> list[Job]:
        opts = settings or self.settings
        opts.ensure_dirs()
        created: list[Job] = []
        for source in sources:
            source = str(source).strip()
            if not source:
                continue
            kind = self._kind_for(source)
            job = Job(
                id=new_id(),
                kind=kind,
                sources=[source],
                title=title,
                bitrate=opts.bitrate,
                settings=opts.to_dict(),
                extra={"pick": pick or None, "bundle": bool(bundle), "force": bool(force)},
            )
            job.label = ARABIC_LABELS["queued"]
            with self._lock:
                self.jobs[job.id] = job
                self.order.append(job.id)
            self._persist(job, force=True)
            self._publish(job, force=True)
            self.queue.put(job.id)
            created.append(job)
        return created

    def submit_upload(
        self,
        job_ids: list[str],
        identifier: str = "",
        settings: Settings | None = None,
        title: str = "",
    ) -> Job:
        opts = settings or self.settings
        job = Job(
            id=new_id(),
            kind="upload",
            sources=list(job_ids),
            title=title or "رفع إلى Archive.org",
            bitrate=opts.bitrate,
            settings=opts.to_dict(),
            extra={"identifier": identifier},
        )
        with self._lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
        self._persist(job, force=True)
        self._publish(job, force=True)
        self.queue.put(job.id)
        return job

    def run_sync(self, job: Job) -> Job:
        """Execute a job in the current thread (used by the CLI)."""
        self._process(job.id)
        return self.jobs.get(job.id, job)

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self, limit: int = 300) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(reversed(self.order))[:limit]
            return [self.jobs[jid].public() for jid in ids if jid in self.jobs]

    def search(self, query: str, limit: int = 300) -> list[dict[str, Any]]:
        needle = str(query or "").strip().lower()
        if not needle:
            return self.list(limit)
        result: list[dict[str, Any]] = []
        for item in self.list(limit=2000):
            haystack = " ".join(
                [
                    str(item.get("title") or ""),
                    " ".join(map(str, item.get("sources") or [])),
                    " ".join(map(str, item.get("outputs") or [])),
                    str(item.get("error") or ""),
                ]
            ).lower()
            if needle in haystack:
                result.append(item)
                if len(result) >= limit:
                    break
        return result

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status not in {"queued", "running"}:
            return False
        token = self.tokens.get(job_id)
        if token:
            token.set()
        job.status = "cancelled"
        job.label = ARABIC_LABELS["cancelled"]
        job.speed = ""
        self._persist(job, force=True)
        self._publish(job, force=True)
        return True

    def retry(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        extra = dict(job.extra or {})
        extra["force"] = True
        new_job = Job(
            id=new_id(),
            kind=job.kind,
            sources=list(job.sources),
            title=job.title,
            bitrate=job.bitrate,
            settings=dict(job.settings),
            extra=extra,
        )
        with self._lock:
            self.jobs[new_job.id] = new_job
            self.order.append(new_job.id)
        self._persist(new_job, force=True)
        self._publish(new_job, force=True)
        self.queue.put(new_job.id)
        return new_job

    def rename(self, job_id: str, title: str) -> tuple[bool, str]:
        job = self.jobs.get(job_id)
        if not job:
            return False, "المهمة غير موجودة"
        title = str(title or "").strip()
        if not title:
            return False, "العنوان فارغ"
        if len(job.outputs) > 1:
            return False, "إعادة التسمية متاحة عند وجود ملف واحد فقط"
        old_path = Path(job.outputs[0]) if job.outputs else None
        if old_path and old_path.is_file():
            target = unique_path(old_path.parent / f"{naming.sanitize(title)}.mp3")
            try:
                move_file(old_path, target)
            except OSError as exc:
                return False, f"تعذّرت إعادة التسمية: {exc}"
            job.outputs = [str(target)]
            job.size = target.stat().st_size
        job.title = title
        self._persist(job, force=True)
        self._publish(job, force=True)
        return True, "تمت إعادة التسمية"

    def delete(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status in {"running", "queued"}:
            self.cancel(job_id)
            deadline = time.monotonic() + 5
            while job_id in self.tokens and time.monotonic() < deadline:
                time.sleep(0.1)
        for output in list(job.outputs):
            try:
                Path(output).unlink()
            except OSError:
                pass
        bundle = (job.extra or {}).get("bundle_path")
        if bundle:
            try:
                Path(bundle).unlink()
            except OSError:
                pass
        keys = [job.seen_key, *((job.extra or {}).get("keys") or [])]
        for key in keys:
            if key:
                self.library.forget(key)
        with self._lock:
            self.jobs.pop(job_id, None)
            if job_id in self.order:
                self.order.remove(job_id)
        try:
            atomic_write_json(
                jobs_file(),
                [self.jobs[jid].to_dict() for jid in self.order if jid in self.jobs],
                mode=0o600,
            )
        except OSError:
            pass
        return True

    def delete_many(self, ids: list[str]) -> int:
        return sum(1 for job_id in ids if self.delete(job_id))

    def clear_finished(self) -> int:
        ids = [
            job.id
            for job in self.jobs.values()
            if job.status in {"done", "error", "cancelled", "interrupted"}
        ]
        return self.delete_many(ids)

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=200)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(subscriber)

    def stats(self) -> dict[str, Any]:
        jobs = list(self.jobs.values())
        done = [job for job in jobs if job.status == "done"]
        return {
            "total": len(jobs),
            "done": len(done),
            "active": sum(1 for job in jobs if job.status in {"queued", "running"}),
            "errors": sum(1 for job in jobs if job.status == "error"),
            "size": sum(job.size for job in done),
            "size_text": human_size(sum(job.size for job in done)),
            "seen": self.library.count(),
            "workers": len(self._threads),
        }

    def disk(self) -> dict[str, Any]:
        usage = shutil.disk_usage(self.settings.output_path)
        output_size = 0
        try:
            for item in self.settings.output_path.rglob("*"):
                if item.is_file():
                    output_size += item.stat().st_size
        except OSError:
            pass
        return {
            "free": usage.free,
            "free_text": human_size(usage.free),
            "total": usage.total,
            "total_text": human_size(usage.total),
            "used": usage.used,
            "output_size": output_size,
            "output_size_text": human_size(output_size),
            "output_dir": str(self.settings.output_path),
        }

    def stop(self) -> None:
        self._stop.set()
        for token in list(self.tokens.values()):
            token.set()
        for _ in self._threads:
            self.queue.put(None)

    # ── workers ────────────────────────────────────────────────────────────

    def _worker(self) -> None:
        while not self._stop.is_set():
            job_id = self.queue.get()
            try:
                if job_id is None:
                    return
                self._process(job_id)
            finally:
                self.queue.task_done()

    def _process_upload(self, job: Job) -> None:
        from . import archive_org

        token = CancelToken()
        self.tokens[job.id] = token
        self._update(
            job,
            status="running",
            label="جارٍ الرفع إلى Archive.org…",
            progress=0.0,
            speed="",
            started_at=now_iso(),
            error="",
        )
        try:
            files: list[str] = []
            for ref_id in job.sources:
                ref = self.jobs.get(ref_id)
                if not ref:
                    continue
                for output in ref.outputs:
                    path = Path(output)
                    if path.is_file() and path.suffix.lower() != ".zip":
                        files.append(str(path))
            if not files:
                raise RuntimeError("لا توجد ملفات مكتملة لرفعها.")

            def report(text: str) -> None:
                self._progress(job, job.progress, text)

            result = archive_org.upload(
                files,
                identifier=str((job.extra or {}).get("identifier") or ""),
                title=job.title,
                on_progress=report,
            )
            job.extra["ia_url"] = result["url"]
            job.extra["identifier"] = result["identifier"]
            job.extra["uploaded"] = len(files)
            self._update(
                job,
                status="done",
                label=f"تم الرفع — {len(files)} ملف",
                progress=1.0,
                completed_at=now_iso(),
                speed="",
            )
        except Exception as exc:
            self._update(
                job,
                status="error",
                label=ARABIC_LABELS["error"],
                error=str(exc) or exc.__class__.__name__,
                completed_at=now_iso(),
                speed="",
            )
        finally:
            self.tokens.pop(job.id, None)

    def _process(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job or job.status == "cancelled":
            return
        settings = Settings.from_dict(job.settings or {})
        settings.ensure_dirs()
        if job.kind == "upload":
            self._process_upload(job)
            return
        token = CancelToken()
        self.tokens[job_id] = token
        self._update(
            job,
            status="running",
            label="جارٍ الفحص…",
            progress=0.0,
            speed="",
            started_at=now_iso(),
            error="",
        )
        workdir = work_root() / job.id
        workdir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        try:
            for source in job.sources:
                if token.is_set():
                    raise transcode.CancelledError("أُلغي")
                if is_url(source):
                    self._handle_url(job, source, settings, token, workdir, errors)
                elif Path(source).is_dir():
                    self._handle_directory(job, Path(source), settings, token, errors)
                else:
                    path = Path(source)
                    if path.suffix.lower() in ARCHIVE_EXTS:
                        self._handle_archive(job, path, settings, token, errors)
                    else:
                        self._handle_file(job, path, settings, token, errors)
                self._persist(job, force=True)

            if token.is_set():
                raise transcode.CancelledError("أُلغي")

            if (job.extra or {}).get("bundle") and len(job.outputs) > 1:
                self._progress(job, 0.99, "جارٍ إنشاء الأرشيف…")
                bundle_name = naming.sanitize(job.title or f"ismp3-{job.id}")
                bundle_path = unique_path(settings.output_path / f"{bundle_name}.zip")
                zip_files(job.outputs, bundle_path)
                job.extra["bundle_path"] = str(bundle_path)
                job.outputs.append(str(bundle_path))

            job.size = sum(
                Path(item).stat().st_size for item in job.outputs if Path(item).is_file()
            )
            job.extra["keys"] = list(job.extra.get("keys") or [])
            if errors:
                job.error = "\n".join(errors[:8])
                self._update(
                    job,
                    status="done" if job.outputs else "error",
                    label="تم مع أخطاء" if job.outputs else ARABIC_LABELS["error"],
                    progress=1.0 if job.outputs else 0.0,
                    completed_at=now_iso(),
                    speed="",
                )
            else:
                self._update(
                    job,
                    status="done" if job.outputs else "error",
                    label="تم" if job.outputs else "لم يُنتج أي ملف",
                    progress=1.0 if job.outputs else 0.0,
                    completed_at=now_iso(),
                    speed="",
                )
                if not job.outputs and not job.error:
                    job.error = "لم يُنتج أي ملف — تحقق من المصدر."
        except (fetch.DownloadCancelled, transcode.CancelledError):
            job.status = "cancelled"
            job.label = ARABIC_LABELS["cancelled"]
            job.speed = ""
            job.completed_at = now_iso()
            self._persist(job, force=True)
            self._publish(job, force=True)
        except Exception as exc:
            job.status = "error"
            job.label = ARABIC_LABELS["error"]
            job.error = str(exc) or exc.__class__.__name__
            job.speed = ""
            job.completed_at = now_iso()
            self._persist(job, force=True)
            self._publish(job, force=True)
        finally:
            self.tokens.pop(job_id, None)
            if not (job.extra or {}).get("keep_temp"):
                shutil.rmtree(workdir, ignore_errors=True)

    # ── source handlers ────────────────────────────────────────────────────

    def _handle_url(
        self,
        job: Job,
        url: str,
        settings: Settings,
        token: CancelToken,
        workdir: Path,
        errors: list[str],
    ) -> None:
        suffix = PurePosixPath(urlparse(url).path).suffix.lower()
        if suffix in ARCHIVE_EXTS:
            local = workdir / f"archive{suffix}"
            self._download_direct(job, url, local, token)
            self._handle_archive(job, local, settings, token, errors)
            return

        # Fast path: a plain YouTube video link does not need a metadata
        # request first. yt-dlp's own extract_info(download=True) already
        # returns the title, so we avoid doing the same work twice.
        if self._is_plain_youtube_video(url, settings):
            self._handle_youtube_single(job, url, settings, token, workdir)
            return

        info = fetch.inspect(url, settings)
        if info.get("kind") == "playlist":
            job.kind = "playlist"
            job.title = info.get("title") or job.title
            job.extra["playlist_title"] = info.get("title") or ""
            entries = list(info.get("entries") or [])
            pick = (job.extra or {}).get("pick")
            if pick:
                wanted = set(int(value) for value in pick)
                entries = [entry for entry in entries if int(entry.get("index", 0)) in wanted]
            total = len(entries)
            job.count = total
            job.extra["total"] = total
            if not total:
                raise RuntimeError("لم يتم اختيار أي مقطع من القائمة.")
            self._update(job, status="running", label=f"قائمة: {job.title}", progress=0.0)

            for position, entry in enumerate(entries, start=1):
                if token.is_set():
                    raise transcode.CancelledError("أُلغي")
                entry_url = entry["url"]
                entry_key = self._entry_key(entry, entry_url)
                if self.library.seen(entry_key) and not (job.extra or {}).get("force"):
                    job.done_count += 1
                    self._progress(
                        job,
                        position / total,
                        f"موجود مسبقاً {position}/{total}",
                    )
                    continue
                self._progress(
                    job,
                    (position - 1) / total,
                    f"تنزيل {position}/{total}",
                )
                try:
                    result = fetch.download_one(
                        entry_url,
                        workdir,
                        settings,
                        token=token,
                        on_progress=lambda fraction, speed, position=position: self._progress(
                            job,
                            ((position - 1) + fraction) / total,
                            f"تنزيل {position}/{total}",
                            speed,
                        ),
                        force=(job.extra or {}).get("force", False),
                    )
                    self._convert_downloaded(
                        job, result, settings, token, position, total, entry_key
                    )
                except fetch.AlreadyDownloaded:
                    job.done_count += 1
                except (fetch.DownloadCancelled, transcode.CancelledError):
                    raise
                except Exception as exc:
                    errors.append(f"{entry.get('title', entry_url)}: {exc}")
                job.done_count = max(job.done_count, position)
                self._persist(job, force=True)
            return

        # single video
        job.kind = "video"
        job.title = info.get("title") or job.title
        key = key_for(info, url)
        job.seen_key = key
        if self.library.seen(key) and not (job.extra or {}).get("force"):
            existing = self.library.get(key) or {}
            existing_path = Path(existing["path"]) if existing.get("path") else None
            if existing_path and existing_path.is_file():
                job.outputs = [str(existing_path)]
                job.size = existing_path.stat().st_size
                job.done_count = 1
                self._update(
                    job,
                    status="done",
                    label="موجود مسبقاً",
                    progress=1.0,
                    completed_at=now_iso(),
                )
                return
            # The file was moved/deleted outside ismp3: forget the stale entry.
            self.library.forget(key)

        self._update(job, status="running", label="جارٍ التنزيل…", count=1)
        try:
            result = fetch.download_one(
                info.get("webpage_url") or url,
                workdir,
                settings,
                token=token,
                on_progress=lambda fraction, speed: self._progress(
                    job, fraction, "جارٍ التنزيل…", speed
                ),
                force=(job.extra or {}).get("force", False),
            )
        except fetch.AlreadyDownloaded:
            job.done_count = 1
            self._progress(job, 1.0, "موجود مسبقاً")
            return
        job.count = 1
        job.done_count = 1
        self._convert_downloaded(job, result, settings, token, 1, 1, key)

    def _entry_key(self, entry: dict[str, Any], url: str) -> str:
        item_id = str(entry.get("id") or "")
        if fetch.is_youtube(url) and item_id:
            return f"youtube:{item_id}"
        return key_for(entry, url)

    def _is_plain_youtube_video(self, url: str, settings: Settings) -> bool:
        """True for ``watch?v=…`` / ``youtu.be/…`` without a playlist to expand."""
        if not fetch.is_youtube(url):
            return False
        low = url.lower()
        if "list=" in low and settings.playlist:
            return False
        if any(part in low for part in ("/playlist", "/@", "/channel/", "/user/", "/c/")):
            return False
        return bool(_youtube_video_id(url))

    def _handle_youtube_single(
        self,
        job: Job,
        url: str,
        settings: Settings,
        token: CancelToken,
        workdir: Path,
    ) -> None:
        item_id = _youtube_video_id(url)
        key = f"youtube:{item_id}"
        job.kind = "video"
        job.seen_key = key
        force = bool((job.extra or {}).get("force"))
        if not force and self.library.seen(key):
            existing = self.library.get(key) or {}
            existing_path = Path(existing["path"]) if existing.get("path") else None
            if existing_path and existing_path.is_file():
                job.title = existing.get("title") or job.title
                job.outputs = [str(existing_path)]
                job.size = existing_path.stat().st_size
                job.count = 1
                job.done_count = 1
                self._update(
                    job,
                    status="done",
                    label="موجود مسبقاً",
                    progress=1.0,
                    completed_at=now_iso(),
                )
                return
            self.library.forget(key)

        self._update(job, status="running", label="جارٍ التنزيل…", count=1)
        try:
            result = fetch.download_one(
                url,
                workdir,
                settings,
                token=token,
                on_progress=lambda fraction, speed: self._progress(
                    job, fraction, "جارٍ التنزيل…", speed
                ),
                force=force,
            )
        except fetch.AlreadyDownloaded:
            job.done_count = 1
            self._progress(job, 1.0, "موجود مسبقاً")
            return
        info = result["info"]
        job.title = info.get("title") or job.title
        job.count = 1
        job.done_count = 1
        self._convert_downloaded(job, result, settings, token, 1, 1, key)

    def _convert_downloaded(
        self,
        job: Job,
        result: dict[str, Any],
        settings: Settings,
        token: CancelToken,
        index: int,
        total: int,
        key: str,
    ) -> None:
        info: dict[str, Any] = result["info"]
        source: Path = result["path"]
        thumb: Path | None = result.get("thumbnail")
        media = probe(source)
        meta = transcode.metadata_from_ytdlp(info)
        merged = dict(info)
        merged.setdefault("playlist_title", (job.extra or {}).get("playlist_title", ""))
        merged.setdefault("playlist_index", index)
        target = naming.build_path(settings.output_path, merged, settings, index=index)

        cover: Path | None = None
        if settings.cover:
            cover_source = thumb or source
            cover = transcode.extract_cover(cover_source, source.with_suffix(".cover.jpg"))
        try:
            self._progress(
                job,
                ((index - 1) + 0.5) / max(total, 1),
                f"تحويل {index}/{total}",
            )
            output = transcode.transcode(
                source,
                target,
                settings,
                metadata=meta,
                duration=media.duration,
                cover=cover,
                on_progress=lambda fraction, speed: self._progress(
                    job,
                    ((index - 1) + fraction) / max(total, 1),
                    f"تحويل {index}/{total}",
                    speed,
                ),
                token=token,
            )
        except Exception:
            raise
        finally:
            for cleanup in (source, thumb, cover):
                if cleanup:
                    try:
                        Path(cleanup).unlink()
                    except OSError:
                        pass

        output = Path(output)
        job.outputs.append(str(output))
        job.size += output.stat().st_size
        job.duration += media.duration
        job.extra.setdefault("keys", []).append(key)
        self.library.remember(key, str(meta.get("title") or output.stem), str(output))
        self._progress(job, index / max(total, 1), f"تم {index}/{total}")

    def _handle_file(
        self,
        job: Job,
        path: Path,
        settings: Settings,
        token: CancelToken,
        errors: list[str],
    ) -> None:
        job.kind = "file"
        if not job.title:
            job.title = path.stem
        job.count = max(job.count, 1)
        try:
            media = probe(path)
            meta = transcode.metadata_from_media(media, path)
            target = naming.build_path(settings.output_path, meta, settings)
            self._progress(job, 0.05, f"تحويل {path.name}")
            output = transcode.transcode_file(
                path,
                target,
                settings,
                on_progress=lambda fraction, speed: self._progress(
                    job, fraction, f"تحويل {path.name}", speed
                ),
                token=token,
            )
            job.outputs.append(str(output))
            job.size += output.stat().st_size
            job.duration += media.duration
            job.done_count = 1
            self._progress(job, 1.0, "تم")
        except (transcode.CancelledError, fetch.DownloadCancelled):
            raise
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")

    def _handle_directory(
        self,
        job: Job,
        folder: Path,
        settings: Settings,
        token: CancelToken,
        errors: list[str],
    ) -> None:
        files = list_media(folder, recursive=True)
        job.kind = "folder"
        job.title = job.title or folder.name
        job.count = len(files)
        if not files:
            raise RuntimeError(f"لا توجد ملفات وسائط في: {folder}")
        total = len(files)
        for index, path in enumerate(files, start=1):
            if token.is_set():
                raise transcode.CancelledError("أُلغي")
            try:
                media = probe(path)
                meta = transcode.metadata_from_media(media, path)
                target = naming.build_path(settings.output_path, meta, settings)
                output = transcode.transcode_file(
                    path,
                    target,
                    settings,
                    on_progress=lambda fraction, speed, i=index: self._progress(
                        job, ((i - 1) + fraction) / total, f"تحويل {i}/{total}", speed
                    ),
                    token=token,
                )
                job.outputs.append(str(output))
                job.size += output.stat().st_size
                job.duration += media.duration
            except (transcode.CancelledError, fetch.DownloadCancelled):
                raise
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
            job.done_count = index
            self._progress(job, index / total, f"تم {index}/{total}")

    def _handle_archive(
        self,
        job: Job,
        path: Path,
        settings: Settings,
        token: CancelToken,
        errors: list[str],
    ) -> None:
        job.kind = "archive"
        job.title = job.title or path.stem
        extract_dir = work_root() / job.id / "extracted"
        shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)
        self._progress(job, 0.02, "جارٍ فك الأرشيف…")
        _extract_archive(path, extract_dir)
        files = list_media(extract_dir, recursive=True)
        if not files:
            raise RuntimeError("الأرشيف لا يحتوي على ملفات صوت/فيديو.")
        total = len(files)
        job.count = total
        for index, item in enumerate(files, start=1):
            if token.is_set():
                raise transcode.CancelledError("أُلغي")
            try:
                media = probe(item)
                meta = transcode.metadata_from_media(media, item)
                target = naming.build_path(settings.output_path, meta, settings)
                output = transcode.transcode_file(
                    item,
                    target,
                    settings,
                    on_progress=lambda fraction, speed, i=index: self._progress(
                        job, ((i - 1) + fraction) / total, f"تحويل {i}/{total}", speed
                    ),
                    token=token,
                )
                job.outputs.append(str(output))
                job.size += output.stat().st_size
                job.duration += media.duration
            except (transcode.CancelledError, fetch.DownloadCancelled):
                raise
            except Exception as exc:
                errors.append(f"{item.name}: {exc}")
            job.done_count = index
            self._progress(job, index / total, f"تم {index}/{total}")

    def _download_direct(
        self,
        job: Job,
        url: str,
        dest: Path,
        token: CancelToken,
    ) -> None:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14) ismp3/0.1"},
        )
        with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if token.is_set():
                    raise transcode.CancelledError("أُلغي")
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if total:
                    self._progress(job, min(done / total, 1.0), "جارٍ تنزيل الأرشيف…")

    # ── helpers ────────────────────────────────────────────────────────────

    def _kind_for(self, source: str) -> str:
        if is_url(source):
            suffix = PurePosixPath(urlparse(source).path).suffix.lower()
            return "archive" if suffix in ARCHIVE_EXTS else "url"
        path = Path(source)
        if path.is_dir():
            return "folder"
        if path.suffix.lower() in ARCHIVE_EXTS:
            return "archive"
        return "file"


def _youtube_video_id(url: str) -> str:
    """Extract the video id from common YouTube URL shapes (no network)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        return parsed.path.strip("/").split("/")[0]
    values = parse_qs(parsed.query).get("v")
    if values:
        return str(values[0])
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
        return parts[1]
    return ""


def _extract_archive(path: Path, dest: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                name = member.filename
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts:
                    continue
                archive.extract(member, dest)
        return
    if suffix in {".tar", ".gz", ".bz2", ".xz"}:
        import tarfile

        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                pure = PurePosixPath(member.name)
                if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk():
                    continue
                archive.extract(member, dest, filter="data")
        return
    tool = shutil.which("7z") or shutil.which("7za") or shutil.which("7zr")
    if not tool:
        raise RuntimeError("ملفات rar/7z تحتاج أداة 7z — ثبّتها أو استخدم zip.")
    result = subprocess.run(
        [tool, "x", "-y", f"-o{dest}", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"فشل فك الأرشيف: {(result.stderr or '').strip()[:300]}")
