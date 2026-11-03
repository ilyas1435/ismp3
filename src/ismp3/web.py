"""Arabic web UI + JSON API on the Python standard library only.

Why not FastAPI? On Termux (Python 3.14/aarch64) pydantic-core cannot build.
``http.server`` is slower, but this is a personal single-user tool.

Routes
------
GET    /                              Arabic single-page app
GET    /static/<file>                 CSS/JS
GET    /api/health                    public health probe
GET    /api/events                    Server-Sent Events (live jobs)
GET    /api/stats, /api/disk
GET    /api/jobs?search=&limit=
POST   /api/jobs                      add sources
GET    /api/jobs/<id>
POST   /api/jobs/<id>/cancel|retry|rename|upload-ia
DELETE /api/jobs/<id>                 delete job + files
POST   /api/jobs/clear                delete finished jobs
GET    /api/jobs/<id>/download?index= download one output
GET    /api/jobs/<id>/zip             download all outputs as ZIP
POST   /api/probe                     inspect URL/file without downloading
GET    /api/browse?path=              browse server folders
POST   /api/upload?name=              raw-body upload of a local file
GET    /api/files                     files in the output folder
GET    /api/settings | POST           read/update settings
GET/POST/DELETE /api/cookies          cookies.txt management
GET/POST/DELETE /api/ia               Archive.org credentials
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import queue
import re
import tempfile
import threading
import time
import traceback
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__, archive_org, fetch, naming, transcode
from .config import (
    Settings,
    config_dir,
    default_cookies_file,
    is_replit,
    is_termux,
    save_settings,
)
from .jobs import JobManager
from .probe import probe
from .util import (
    MEDIA_EXTS,
    atomic_write_text,
    estimate_size,
    human_size,
    is_url,
    parse_pick,
    within_roots,
    zip_files,
)

MAX_JSON = 2 * 1024 * 1024
STATIC_DIR = Path(__file__).with_name("static")
COOKIE_NAME = "ismp3_token"
VERSION = __version__


def _roots(settings: Settings) -> list[Path]:
    candidates = [
        Path.home(),
        Path("/sdcard"),
        Path("/storage/emulated/0"),
        Path(tempfile.gettempdir()),
        Path.cwd(),
        settings.output_path,
        config_dir(),
    ]
    seen: list[Path] = []
    for item in candidates:
        try:
            resolved = item.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if resolved.is_dir() and resolved not in seen:
            seen.append(resolved)
    return seen


class App:
    def __init__(self, settings: Settings, token: str = "") -> None:
        settings.ensure_dirs()
        self.settings = settings
        self.token = token or ""
        self.manager = JobManager(settings)
        self.roots = _roots(settings)
        self.started = time.time()
        self._lock = threading.Lock()

    def refresh_roots(self) -> None:
        self.roots = _roots(self.settings)

    def stop(self) -> None:
        self.manager.stop()


class Handler(BaseHTTPRequestHandler):
    server_version = f"ismp3/{VERSION}"
    protocol_version = "HTTP/1.1"

    # ── plumbing ───────────────────────────────────────────────────────────

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("ISMP3_DEBUG"):
            super().log_message(fmt, *args)

    def _query(self) -> dict[str, str]:
        return {key: values[-1] for key, values in urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query, keep_blank_values=True
        ).items()}

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for chunk in raw.split(";"):
            name, _, value = chunk.strip().partition("=")
            if name == COOKIE_NAME:
                return value
        return ""

    def _authorized(self, query: dict[str, str]) -> bool:
        expected = self.app.token
        if not expected:
            return True
        header = self.headers.get("Authorization") or ""
        bearer = header[7:].strip() if header.lower().startswith("bearer ") else ""
        supplied = query.get("token") or bearer or self._cookie_token()
        return bool(supplied) and hmac.compare_digest(str(supplied), expected)

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _body(self, limit: int = MAX_JSON) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        if length > limit:
            raise ValueError(f"الطلب كبير جداً (الحد {human_size(limit)}).")
        return self.rfile.read(length)

    def _json_body(self) -> dict[str, Any]:
        raw = self._body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"JSON غير صالح: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("يجب أن يكون الطلب كائن JSON.")
        return data

    def _parts(self) -> list[str]:
        path = urllib.parse.urlparse(self.path).path
        return [urllib.parse.unquote(part) for part in path.split("/") if part]

    def _static(self, name: str) -> None:
        base = STATIC_DIR.resolve()
        target = (base / name).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            self._error("مسار غير مسموح", 403)
            return
        if not target.is_file():
            self._error("غير موجود", 404)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    # ── HTTP methods ───────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._route_get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._internal()

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._route_post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._internal()

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            self._route_delete()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._internal()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _internal(self) -> None:
        if os.environ.get("ISMP3_DEBUG"):
            traceback.print_exc()
        try:
            self._error("خطأ داخلي في الخادم", 500)
        except Exception:
            pass

    # ── GET ────────────────────────────────────────────────────────────────

    def _route_get(self) -> None:
        parts = self._parts()
        query = self._query()

        if not parts:
            self._static("index.html")
            return
        if parts[0] == "favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            return
        if parts[0] == "static" and len(parts) >= 2:
            self._static("/".join(parts[1:]))
            return
        if parts[0] != "api":
            self._error("غير موجود", 404)
            return
        if parts[1:2] == ["health"]:
            self._health()
            return
        if not self._authorized(query):
            self._error("رمز الدخول مطلوب — افتح الرابط مع ?token=…", 401)
            return

        route = parts[1:]
        if route == ["events"]:
            self._events()
        elif route == ["stats"]:
            self._json({"stats": self.app.manager.stats(), "version": VERSION})
        elif route == ["disk"]:
            self._json(self.app.manager.disk())
        elif route == ["jobs"]:
            self._json({"jobs": self._jobs(query)})
        elif len(route) >= 2 and route[0] == "jobs":
            self._job_get(route[1:])
        elif route == ["browse"]:
            self._browse(query)
        elif route == ["files"]:
            self._files()
        elif route == ["files", "download"]:
            self._file_download(query)
        elif route == ["settings"]:
            self._json(self._public_settings())
        elif route == ["cookies"]:
            self._cookies_status()
        elif route == ["ia"]:
            self._json(archive_org.credentials_status())
        elif route == ["library"]:
            self._json(self._library())
        else:
            self._error("مسار غير معروف", 404)

    def _health(self) -> None:
        self._json(
            {
                "ok": True,
                "version": VERSION,
                "uptime": int(time.time() - self.app.started),
                "termux": is_termux(),
                "replit": is_replit(),
                "workers": self.app.manager.stats().get("workers", 0),
            }
        )

    def _jobs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        manager = self.app.manager
        search = query.get("search", "").strip()
        try:
            limit = max(1, min(2000, int(query.get("limit", "300"))))
        except ValueError:
            limit = 300
        jobs = manager.search(search, limit=limit) if search else manager.list(limit=limit)
        status = query.get("status", "").strip()
        if status == "active":
            jobs = [job for job in jobs if job.get("status") in {"queued", "running"}]
        elif status:
            jobs = [job for job in jobs if job.get("status") == status]
        return jobs

    def _public_settings(self) -> dict[str, Any]:
        data = self.app.settings.to_dict()
        data["token"] = "***" if self.app.token else ""
        data["auth_required"] = bool(self.app.token)
        data["config_file"] = str(config_dir() / "config.toml")
        return data

    def _job_get(self, rest: list[str]) -> None:
        job_id = rest[0]
        manager = self.app.manager
        if len(rest) == 1:
            job = manager.get(job_id)
            if not job:
                self._error("المهمة غير موجودة", 404)
                return
            self._json(job.public())
            return
        action = rest[1]
        if action == "download":
            self._job_download(job_id)
        elif action == "zip":
            self._job_zip(job_id)
        else:
            self._error("مسار غير معروف", 404)

    def _job_download(self, job_id: str) -> None:
        job = self.app.manager.get(job_id)
        if not job or not job.outputs:
            self._error("لا توجد ملفات لهذه المهمة", 404)
            return
        query = self._query()
        try:
            index = int(query.get("index", "0"))
        except ValueError:
            index = 0
        outputs = [item for item in job.outputs if Path(item).is_file()]
        if not outputs:
            self._error("الملفات لم تعد موجودة", 404)
            return
        index = max(0, min(index, len(outputs) - 1))
        self._send_file(Path(outputs[index]), download=True)

    def _job_zip(self, job_id: str) -> None:
        job = self.app.manager.get(job_id)
        if not job or not job.outputs:
            self._error("لا توجد ملفات لهذه المهمة", 404)
            return
        files = [item for item in job.outputs if Path(item).is_file()]
        if not files:
            self._error("الملفات لم تعد موجودة", 404)
            return
        bundle = (job.extra or {}).get("bundle_path")
        if bundle and Path(bundle).is_file():
            self._send_file(Path(bundle), download=True)
            return
        fd, temp_name = tempfile.mkstemp(prefix=f"ismp3-{job.id}-", suffix=".zip")
        os.close(fd)
        temp = Path(temp_name)
        try:
            zip_files(files, temp)
            self._send_file(temp, download=True, name=f"{naming.sanitize(job.title or job.id)}.zip")
        finally:
            try:
                temp.unlink()
            except OSError:
                pass

    def _browse(self, query: dict[str, str]) -> None:
        raw = query.get("path", "").strip()
        if not raw:
            base = Path.home()
        else:
            base = Path(raw).expanduser()
            if not within_roots(base, self.app.roots):
                self._error("مسار خارج المجلدات المسموحة", 403)
                return
        try:
            base = base.resolve()
        except (OSError, RuntimeError):
            self._error("مسار غير صالح", 400)
            return
        if not base.is_dir():
            self._error("المجلد غير موجود", 404)
            return
        dirs: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        try:
            children = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            self._error(f"تعذّرت قراءة المجلد: {exc}", 403)
            return
        for child in children[:2000]:
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    dirs.append({"name": child.name, "path": str(child)})
                elif child.is_file() and child.suffix.lower() in MEDIA_EXTS:
                    files.append(
                        {
                            "name": child.name,
                            "path": str(child),
                            "size": child.stat().st_size,
                            "size_text": human_size(child.stat().st_size),
                        }
                    )
            except OSError:
                continue
        parent = str(base.parent) if base != base.parent else ""
        if parent and not within_roots(parent, self.app.roots):
            parent = ""
        self._json(
            {
                "path": str(base),
                "parent": parent,
                "dirs": dirs,
                "files": files,
                "roots": [str(root) for root in self.app.roots],
            }
        )

    def _files(self) -> None:
        base = self.app.settings.output_path
        items: list[dict[str, Any]] = []
        try:
            for path in sorted(base.rglob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
                if not path.is_file() or path.suffix.lower() != ".mp3":
                    continue
                stat = path.stat()
                items.append(
                    {
                        "name": path.name,
                        "path": str(path),
                        "size": stat.st_size,
                        "size_text": human_size(stat.st_size),
                        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                        "download": f"/api/files/download?path={urllib.parse.quote(str(path))}",
                    }
                )
                if len(items) >= 1000:
                    break
        except OSError as exc:
            self._error(f"تعذّرت قراءة المخرجات: {exc}", 500)
            return
        self._json({"dir": str(base), "files": items})

    def _library(self) -> list[dict[str, Any]]:
        library = self.app.manager.library
        raw = library.snapshot()
        items: list[dict[str, Any]] = []
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            items.append(
                {
                    "key": key,
                    "title": value.get("title", ""),
                    "path": value.get("path", ""),
                    "at": value.get("at", ""),
                }
            )
        items.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
        return items

    def _file_download(self, query: dict[str, str]) -> None:
        raw = query.get("path", "").strip()
        if not raw:
            self._error("حدّد مسار الملف", 400)
            return
        path = Path(raw).expanduser()
        if not within_roots(path, [self.app.settings.output_path]):
            self._error("مسار خارج مجلد المخرجات", 403)
            return
        self._send_file(path, download=True)

    def _events(self) -> None:
        manager = self.app.manager
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        subscriber = manager.subscribe()
        try:
            self.wfile.write(
                f"data: {json.dumps({'type': 'hello', 'version': VERSION, 'stats': manager.stats()}, ensure_ascii=False)}\n\n".encode()
            )
            self.wfile.flush()
            while True:
                try:
                    event = subscriber.get(timeout=20)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(event, ensure_ascii=False, default=str)
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            manager.unsubscribe(subscriber)

    def _cookies_status(self) -> None:
        path = self.app.settings.cookies_path
        if not path or not path.is_file():
            self._json({"configured": False, "path": str(path or default_cookies_file()), "count": 0})
            return
        try:
            lines = [
                line
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            stat = path.stat()
        except OSError as exc:
            self._error(f"تعذّرت قراءة الكوكيز: {exc}", 500)
            return
        self._json(
            {
                "configured": True,
                "path": str(path),
                "count": len(lines),
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
        )

    # ── POST ───────────────────────────────────────────────────────────────

    def _route_post(self) -> None:
        parts = self._parts()
        query = self._query()
        if parts[:1] != ["api"]:
            self._error("غير موجود", 404)
            return
        if not self._authorized(query):
            self._error("رمز الدخول مطلوب", 401)
            return
        route = parts[1:]

        if route == ["jobs"]:
            self._add_jobs()
        elif route == ["jobs", "clear"]:
            count = self.app.manager.clear_finished()
            self._json({"deleted": count})
        elif len(route) == 3 and route[0] == "jobs":
            self._job_action(route[1], route[2])
        elif route == ["probe"]:
            self._probe_route()
        elif route == ["upload"]:
            self._upload(query)
        elif route == ["settings"]:
            self._update_settings()
        elif route == ["cookies"]:
            self._cookies_save()
        elif route == ["ia"]:
            self._ia_save()
        elif route == ["upload-ia"]:
            self._upload_ia()
        else:
            self._error("مسار غير معروف", 404)

    def _add_jobs(self) -> None:
        data = self._json_body()
        sources = data.get("sources")
        if isinstance(sources, str):
            sources = [line for line in sources.splitlines() if line.strip()]
        if not sources and data.get("source"):
            sources = [str(data["source"])]
        if not isinstance(sources, list) or not sources:
            self._error("أدخل رابطاً أو مسار ملف واحداً على الأقل", 400)
            return
        sources = [str(item).strip() for item in sources if str(item).strip()]
        if not sources:
            self._error("أدخل رابطاً أو مسار ملف واحداً على الأقل", 400)
            return

        overrides: dict[str, Any] = {}
        allowed = {
            "bitrate", "mode", "sample_rate", "channels", "cover", "layout",
            "speed", "playlist", "audio_lang", "client", "cookies_file",
            "output_dir", "jobs", "retries", "fragment_jobs", "rate_limit", "proxy",
        }
        raw_settings = data.get("settings")
        if isinstance(raw_settings, dict):
            for key, value in raw_settings.items():
                if key in allowed and value is not None:
                    overrides[key] = value
        if "bitrate" in data:
            overrides["bitrate"] = data["bitrate"]
        try:
            settings = self.app.settings.with_overrides(**overrides)
        except (TypeError, ValueError) as exc:
            self._error(f"إعدادات غير صالحة: {exc}", 400)
            return
        try:
            transcode.validate(settings.bitrate, settings.sample_rate)
        except ValueError as exc:
            self._error(str(exc), 400)
            return
        try:
            pick = parse_pick(data.get("pick"))
        except (TypeError, ValueError) as exc:
            self._error(f"اختيار غير صالح: {exc}", 400)
            return
        jobs = self.app.manager.submit(
            sources,
            settings=settings,
            pick=pick,
            bundle=bool(data.get("bundle")),
            force=bool(data.get("force")),
            title=str(data.get("title") or ""),
        )
        self._json({"jobs": [job.public() for job in jobs]}, 201)

    def _job_action(self, job_id: str, action: str) -> None:
        manager = self.app.manager
        if action == "cancel":
            self._json({"ok": manager.cancel(job_id)})
        elif action == "retry":
            job = manager.retry(job_id)
            if not job:
                self._error("المهمة غير موجودة", 404)
                return
            self._json(job.public(), 201)
        elif action == "rename":
            data = self._json_body()
            ok, message = manager.rename(job_id, str(data.get("title") or ""))
            if ok:
                job = manager.get(job_id)
                self._json({"ok": True, "message": message, "job": job.public() if job else None})
            else:
                self._error(message, 400)
        elif action == "upload-ia":
            data = self._json_body()
            job = manager.submit_upload(
                [job_id],
                identifier=str(data.get("identifier") or ""),
                title=str(data.get("title") or ""),
            )
            self._json(job.public(), 201)
        else:
            self._error("إجراء غير معروف", 404)

    def _probe_route(self) -> None:
        data = self._json_body()
        source = str(data.get("source") or "").strip()
        if not source:
            self._error("أدخل رابطاً أو مساراً", 400)
            return
        overrides = {}
        if data.get("bitrate"):
            overrides["bitrate"] = data["bitrate"]
        settings = self.app.settings.with_overrides(**overrides)
        try:
            if is_url(source):
                info = fetch.inspect(source, settings)
                if info.get("kind") == "playlist":
                    entries = info.get("entries") or []
                    duration = sum(float(item.get("duration") or 0) for item in entries)
                    self._json(
                        {
                            "kind": "playlist",
                            "title": info.get("title"),
                            "count": len(entries),
                            "duration": duration,
                            "estimate": estimate_size(duration, settings.bitrate),
                            "entries": entries,
                        }
                    )
                else:
                    duration = float(info.get("duration") or 0)
                    self._json(
                        {
                            "kind": "video",
                            "title": info.get("title"),
                            "uploader": info.get("uploader"),
                            "duration": duration,
                            "count": 1,
                            "estimate": estimate_size(duration, settings.bitrate),
                        }
                    )
            else:
                path = Path(source).expanduser()
                if not within_roots(path, self.app.roots):
                    self._error("مسار خارج المجلدات المسموحة", 403)
                    return
                media = probe(path)
                self._json(
                    {
                        "kind": "file",
                        "title": media.tags.get("title") or path.stem,
                        "duration": media.duration,
                        "count": 1,
                        "size": media.size,
                        "estimate": estimate_size(media.duration, settings.bitrate),
                    }
                )
        except Exception as exc:
            self._error(str(exc), 400)

    def _upload(self, query: dict[str, str]) -> None:
        name = naming.sanitize(query.get("name") or "upload")
        if not Path(name).suffix:
            name = f"{name}.bin"
        # Use the per-user cache, not the public Music folder.
        from .config import uploads_dir
        from .util import new_id

        target_dir = uploads_dir() / f"{new_id()}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._error("الملف فارغ", 400)
            return
        if length > 4 * 1024 * 1024 * 1024:
            self._error("الملف أكبر من 4 GB", 413)
            return
        written = 0
        try:
            with target.open("wb") as handle:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
        except OSError as exc:
            try:
                target.unlink()
            except OSError:
                pass
            self._error(f"تعذّر حفظ الملف: {exc}", 500)
            return
        if written != length:
            try:
                target.unlink()
            except OSError:
                pass
            self._error("انقطع رفع الملف", 400)
            return
        self._json(
            {
                "path": str(target),
                "name": target.name,
                "size": written,
                "size_text": human_size(written),
            },
            201,
        )

    def _update_settings(self) -> None:
        data = self._json_body()
        allowed = {
            "output_dir", "bitrate", "mode", "sample_rate", "channels", "cover",
            "layout", "speed", "playlist", "jobs", "client", "audio_lang",
            "cookies_file", "token", "retries", "fragment_jobs", "rate_limit", "proxy",
        }
        current = self.app.settings.to_dict()
        for key, value in data.items():
            if key in allowed and value is not None:
                current[key] = value
        try:
            settings = Settings.from_dict(current)
            transcode.validate(settings.bitrate, settings.sample_rate)
        except (TypeError, ValueError) as exc:
            self._error(f"إعدادات غير صالحة: {exc}", 400)
            return
        try:
            settings.ensure_dirs()
        except OSError as exc:
            self._error(f"تعذّر إنشاء مجلد المخرجات: {exc}", 400)
            return
        path = save_settings(settings)
        self.app.settings = settings
        self.app.manager.settings = settings
        if "token" in data:
            self.app.token = str(data.get("token") or "")
        self.app.refresh_roots()
        result = self._public_settings()
        result["saved_to"] = str(path)
        self._json(result)

    def _cookies_save(self) -> None:
        data = self._json_body()
        text = str(data.get("text") or "").strip()
        if not text:
            self._error("الصق محتوى cookies.txt أولاً", 400)
            return
        lines = [
            line for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not lines or not any(len(line.split("\t")) >= 6 for line in lines):
            self._error("لا يبدو ملف Netscape cookies.txt صالحاً", 400)
            return
        target = self.app.settings.cookies_path or default_cookies_file()
        try:
            atomic_write_text(target, text.rstrip() + "\n", mode=0o600)
        except OSError as exc:
            self._error(f"تعذّر حفظ الكوكيز: {exc}", 500)
            return
        if not self.app.settings.cookies_file:
            self.app.settings.cookies_file = str(target)
            try:
                save_settings(self.app.settings)
            except OSError:
                pass
        self._cookies_status()

    def _cookies_delete(self) -> None:
        target = self.app.settings.cookies_path
        if target:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._error(f"تعذّر حذف الكوكيز: {exc}", 500)
                return
        self._json({"ok": True})

    def _ia_save(self) -> None:
        data = self._json_body()
        try:
            archive_org.save_credentials(
                str(data.get("access") or ""), str(data.get("secret") or "")
            )
        except (archive_org.ArchiveError, OSError) as exc:
            self._error(str(exc), 400)
            return
        self._json(archive_org.credentials_status())

    def _upload_ia(self) -> None:
        data = self._json_body()
        ids = data.get("ids") or []
        if isinstance(ids, str):
            ids = [ids]
        if not ids:
            self._error("حدّد مهمة واحدة على الأقل", 400)
            return
        job = self.app.manager.submit_upload(
            [str(item) for item in ids],
            identifier=str(data.get("identifier") or ""),
            title=str(data.get("title") or ""),
        )
        self._json(job.public(), 201)

    # ── DELETE ─────────────────────────────────────────────────────────────

    def _route_delete(self) -> None:
        parts = self._parts()
        query = self._query()
        if parts[:1] != ["api"]:
            self._error("غير موجود", 404)
            return
        if not self._authorized(query):
            self._error("رمز الدخول مطلوب", 401)
            return
        route = parts[1:]
        if len(route) == 2 and route[0] == "jobs":
            ok = self.app.manager.delete(route[1])
            self._json({"ok": ok})
        elif route == ["jobs"]:
            data = self._json_body()
            ids = data.get("ids") or []
            if isinstance(ids, str):
                ids = [ids]
            count = self.app.manager.delete_many([str(item) for item in ids])
            self._json({"deleted": count})
        elif route == ["cookies"]:
            self._cookies_delete()
        elif route == ["ia"]:
            archive_org.clear_credentials()
            self._json({"ok": True})
        else:
            self._error("مسار غير معروف", 404)

    # ── file sending ───────────────────────────────────────────────────────

    def _send_file(self, path: Path, download: bool = False, name: str = "") -> None:
        try:
            path = path.resolve()
            stat = path.stat()
        except (OSError, RuntimeError):
            self._error("الملف غير موجود", 404)
            return
        if not path.is_file():
            self._error("الملف غير موجود", 404)
            return
        filename = name or path.name
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if filename.lower().endswith(".mp3"):
            ctype = "audio/mpeg"
        elif filename.lower().endswith(".zip"):
            ctype = "application/zip"
        elif filename.lower().endswith(".jpg"):
            ctype = "image/jpeg"

        start, end = 0, stat.st_size - 1
        status = 200
        range_header = self.headers.get("Range") or ""
        match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
        if match and stat.st_size > 0:
            left, right = match.group(1), match.group(2)
            if left:
                start = int(left)
                if right:
                    end = min(int(right), stat.st_size - 1)
            elif right:
                start = max(0, stat.st_size - int(right))
            if start <= end < stat.st_size:
                status = 206
            else:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{stat.st_size}")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                return
        length = end - start + 1

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{stat.st_size}")
        disposition = "attachment" if download else "inline"
        ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "file"
        quoted = urllib.parse.quote(filename)
        self.send_header(
            "Content-Disposition",
            f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}",
        )
        self.send_header("Cache-Control", "private, max-age=60")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    app: App


def _generate_token() -> str:
    import secrets

    return secrets.token_urlsafe(18)


def serve(
    settings: Settings,
    host: str = "",
    port: int = 0,
    token: str = "",
    open_browser: bool = True,
) -> int:
    settings.ensure_dirs()
    port = int(port or settings.port or 8787)
    host = str(host or settings.host or "127.0.0.1").strip()
    env_port = os.environ.get("PORT")
    if env_port and (is_replit() or host in {"0.0.0.0", ""}):
        try:
            port = int(env_port)
        except ValueError:
            pass
    if is_replit() and host in {"127.0.0.1", "localhost", ""}:
        host = "0.0.0.0"

    token = str(token or settings.token or "").strip()
    external = host not in {"127.0.0.1", "localhost", "::1"}
    if external and not token:
        token = _generate_token()
        print("🔐 تم توليد رمز دخول لأن الخادم مفتوح للشبكة.")

    app = App(settings, token=token)
    server = Server((host, port), Handler)
    server.app = app
    shown_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    query = f"?token={urllib.parse.quote(app.token)}" if app.token else ""
    url = f"http://{shown_host}:{server.server_port}/{query}"
    print("─" * 60)
    print(f"  ismp3 {VERSION} — واجهة عربية")
    print(f"  المخرجات: {settings.output_path}")
    print(f"  الرابط:   {url}")
    if external:
        domain = os.environ.get("REPLIT_DEV_DOMAIN") or ""
        if domain:
            print(f"  Replit:   https://{domain}/{query}")
    if app.token:
        print("  رمز الدخول إلزامي (تجاهله في الرابط أعلاه).")
    print("  Ctrl+C للإيقاف.")
    print("─" * 60)

    if open_browser:
        def _open() -> None:
            time.sleep(0.8)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nإلى اللقاء.")
    finally:
        app.stop()
        server.server_close()
    return 0


__all__ = ["serve", "App", "Handler", "Server"]
