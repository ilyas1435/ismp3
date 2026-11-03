from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ismp3.config import Settings
from ismp3.web import App, Handler, Server

FFMPEG = shutil.which("ffmpeg")


def http(
    base: str,
    path: str,
    method: str = "GET",
    payload: Any = None,
    raw: bytes | None = None,
    token: str = "",
) -> tuple[int, Any]:
    headers: dict[str, str] = {}
    data: bytes | None = raw
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            if not body:
                return response.status, None
            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type:
                return response.status, json.loads(body.decode("utf-8"))
            return response.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return exc.code, body


@contextlib.contextmanager
def running_app(tmp_path: Path, token: str = "") -> Iterator[tuple[str, App]]:
    settings = Settings(output_dir=str(tmp_path / "out"), bitrate=24, jobs=1)
    app = App(settings, token=token)
    server = Server(("127.0.0.1", 0), Handler)
    server.app = app
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base, app
    finally:
        server.shutdown()
        server.server_close()
        app.stop()
        thread.join(timeout=5)


def make_wav(path: Path, seconds: float = 1.0) -> Path:
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


def test_health_is_public(tmp_path: Path, isolated: Path) -> None:
    with running_app(tmp_path, token="secret") as (base, _app):
        status, data = http(base, "/api/health")
        assert status == 200
        assert data["ok"] is True
        assert data["version"]


def test_index_and_static(tmp_path: Path, isolated: Path) -> None:
    with running_app(tmp_path, token="secret") as (base, _app):
        for path, needle in (
            ("/", "ismp3"),
            ("/static/style.css", "--accent"),
            ("/static/app.js", "addJobs"),
        ):
            request = urllib.request.Request(base + path)
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 200
                body = response.read().decode("utf-8")
                assert needle in body


def test_token_required(tmp_path: Path, isolated: Path) -> None:
    with running_app(tmp_path, token="secret") as (base, _app):
        status, data = http(base, "/api/stats")
        assert status == 401
        status, data = http(base, "/api/stats", token="secret")
        assert status == 200
        assert "stats" in data


def test_settings_round_trip(tmp_path: Path, isolated: Path) -> None:
    out = tmp_path / "custom-out"
    with running_app(tmp_path) as (base, app):
        status, data = http(base, "/api/settings")
        assert status == 200
        assert data["bitrate"] == 24

        status, data = http(
            base,
            "/api/settings",
            method="POST",
            payload={"bitrate": 32, "output_dir": str(out), "jobs": 1},
        )
        assert status == 200, data
        assert data["bitrate"] == 32
        assert data["output_dir"] == str(out)
        assert app.settings.output_path == out
        assert out.is_dir()


def test_upload_and_browse(tmp_path: Path, isolated: Path) -> None:
    wav = make_wav(tmp_path / "clip.wav")
    with running_app(tmp_path) as (base, _app):
        status, data = http(
            base,
            "/api/upload?name=" + urllib.parse.quote("clip.wav"),
            method="POST",
            raw=wav.read_bytes(),
        )
        assert status == 201, data
        uploaded = Path(data["path"])
        assert uploaded.is_file()
        assert uploaded.stat().st_size == wav.stat().st_size

        parent = urllib.parse.quote(str(uploaded.parent))
        status, listing = http(base, f"/api/browse?path={parent}")
        assert status == 200, listing
        names = [Path(item["path"]).name for item in listing["files"]]
        assert uploaded.name in names


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg غير مثبّت")
def test_web_job_end_to_end(tmp_path: Path, isolated: Path) -> None:
    wav = make_wav(tmp_path / "voice.wav")
    with running_app(tmp_path) as (base, _app):
        status, data = http(
            base,
            "/api/upload?name=" + urllib.parse.quote("voice.wav"),
            method="POST",
            raw=wav.read_bytes(),
        )
        assert status == 201, data
        source = data["path"]

        status, created = http(
            base,
            "/api/jobs",
            method="POST",
            payload={
                "sources": [source],
                "settings": {"bitrate": 24, "channels": 1, "mode": "cbr"},
            },
        )
        assert status == 201, created
        job = created["jobs"][0]
        job_id = job["id"]

        deadline = time.monotonic() + 45
        final = job
        while time.monotonic() < deadline:
            status, final = http(base, f"/api/jobs/{job_id}")
            assert status == 200
            if final["status"] in {"done", "error", "cancelled"}:
                break
            time.sleep(0.25)

        assert final["status"] == "done", final.get("error")
        assert final["outputs"]
        output = Path(final["outputs"][0])
        assert output.is_file()
        assert output.suffix == ".mp3"

        status, listing = http(base, "/api/files")
        assert status == 200
        assert any(Path(item["path"]) == output for item in listing["files"])

        status, deleted = http(base, f"/api/jobs/{job_id}", method="DELETE")
        assert status == 200
        assert deleted["ok"] is True
        assert not output.exists()
