"""Configuration: one TOML file, no database, no hidden state."""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

APP_NAME = "ismp3"

VALID_MODES = {"cbr", "abr", "vbr"}
VALID_LAYOUTS = {"flat", "artist", "album", "playlist"}
VALID_CLIENTS = {"auto", "android", "ios", "tv", "web", "mweb", "android_testsuite", "direct"}


def is_termux() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "") or bool(os.environ.get("TERMUX_VERSION"))


def is_replit() -> bool:
    return bool(
        os.environ.get("REPL_ID")
        or os.environ.get("REPLIT_DEV_DOMAIN")
        or os.environ.get("REPLIT_DB_URL")
    )


def _xdg(env_name: str, fallback: Path) -> Path:
    base = os.environ.get(env_name)
    return (Path(base) if base else fallback) / APP_NAME


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", Path.home() / ".local" / "share")


def cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / APP_NAME


def default_output_dir() -> Path:
    if is_termux():
        music = Path("/sdcard/Music")
        if music.is_dir():
            return music / APP_NAME
    if is_replit():
        return Path.cwd() / "data" / "downloads"
    return Path.home() / "Music" / APP_NAME


def jobs_file() -> Path:
    return data_dir() / "jobs.json"


def seen_file() -> Path:
    return data_dir() / "seen.json"


def archive_file() -> Path:
    return data_dir() / "archive.txt"


def ia_credentials_file() -> Path:
    return data_dir() / "ia.json"


def default_cookies_file() -> Path:
    return data_dir() / "cookies.txt"


def work_root() -> Path:
    return cache_dir() / "work"


def uploads_dir() -> Path:
    return cache_dir() / "uploads"


def config_file() -> Path:
    return Path(os.environ.get("ISMP3_CONFIG", config_dir() / "config.toml"))


@dataclass
class Settings:
    output_dir: str = ""
    bitrate: int = 24
    mode: str = "cbr"
    sample_rate: int = 0  # 0 = auto
    channels: int = 1
    speed: float = 1.0
    cover: bool = False
    layout: str = "flat"
    jobs: int = 2
    client: str = "auto"
    audio_lang: str = "original"
    playlist: bool = True
    cookies_file: str = ""
    host: str = "127.0.0.1"
    port: int = 8787
    retries: int = 3
    fragment_jobs: int = 4
    rate_limit: str = ""
    proxy: str = ""

    def __post_init__(self) -> None:
        if not self.output_dir:
            self.output_dir = str(default_output_dir())
        self.normalize()

    def normalize(self) -> None:
        self.output_dir = str(Path(str(self.output_dir)).expanduser())
        self.bitrate = int(max(8, min(320, int(self.bitrate))))
        self.mode = str(self.mode).lower()
        if self.mode not in VALID_MODES:
            self.mode = "cbr"
        self.sample_rate = int(self.sample_rate)
        if self.sample_rate not in {0, 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000}:
            self.sample_rate = 0
        self.channels = 1 if int(self.channels) == 1 else 2
        self.speed = float(max(0.5, min(4.0, float(self.speed))))
        self.cover = bool(self.cover)
        self.layout = str(self.layout).lower()
        if self.layout not in VALID_LAYOUTS:
            self.layout = "flat"
        self.jobs = int(max(1, min(8, int(self.jobs))))
        self.client = str(self.client).lower()
        if self.client not in VALID_CLIENTS:
            self.client = "auto"
        self.audio_lang = str(self.audio_lang or "original")
        self.playlist = bool(self.playlist)
        self.cookies_file = str(self.cookies_file or "")
        self.host = str(self.host or "127.0.0.1")
        self.port = int(max(1, min(65535, int(self.port))))
        self.retries = int(max(0, min(10, int(self.retries))))
        self.fragment_jobs = int(max(1, min(16, int(self.fragment_jobs))))
        self.rate_limit = str(self.rate_limit or "")
        self.proxy = str(self.proxy or "")

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir).expanduser()

    @property
    def cookies_path(self) -> Path | None:
        if self.cookies_file:
            return Path(self.cookies_file).expanduser()
        legacy = default_cookies_file()
        return legacy if legacy.is_file() else None

    def ensure_dirs(self) -> None:
        self.output_path.mkdir(parents=True, exist_ok=True)
        data_dir().mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(data_dir(), 0o700)
        except OSError:
            pass
        work_root().mkdir(parents=True, exist_ok=True)
        uploads_dir().mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def with_overrides(self, **overrides: Any) -> Settings:
        data = self.to_dict()
        for key, value in overrides.items():
            if value is not None and key in data:
                data[key] = value
        return Settings.from_dict(data)


def load_settings(path: str | Path | None = None) -> Settings:
    target = Path(path) if path else config_file()
    raw: dict[str, Any] = {}
    if target.is_file():
        try:
            with target.open("rb") as fh:
                raw = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            raw = {}
    return Settings.from_dict(raw)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def save_settings(settings: Settings, path: str | Path | None = None) -> Path:
    target = Path(path) if path else config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# ismp3 — إعدادات", "# عدّل من الواجهة أو CLI ثم أعد التشغيل إن لزم.", ""]
    for key, value in asdict(settings).items():
        lines.append(f"{key} = {_toml_value(value)}")
    lines.append("")
    from .util import atomic_write_text

    atomic_write_text(target, "\n".join(lines), mode=0o600)
    return target
