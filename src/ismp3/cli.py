"""Command line interface (Arabic UI, English command names)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings, config_file, default_cookies_file, load_settings, save_settings
from .util import format_duration, human_size, is_url, parse_pick

BITRATES = [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
LAYOUTS = ["flat", "artist", "album", "playlist"]
CLIENTS = ["auto", "android", "ios", "tv", "web", "mweb", "android_testsuite", "direct"]
COMMANDS = {
    "add", "a", "probe", "list", "ls", "rename", "rm", "retry", "doctor",
    "update", "serve", "cookies", "upload-ia", "config", "version",
}


class ConsoleProgress:
    def __init__(self, stream: Any = None) -> None:
        self.stream = stream or sys.stderr
        self.last_print = 0.0
        self.last_status = ""
        self.interactive = self.stream.isatty()

    def __call__(self, event: dict[str, Any]) -> None:
        job = event.get("job") or {}
        status = str(job.get("status") or "")
        label = str(job.get("label") or "")
        speed = str(job.get("speed") or "")
        progress = float(job.get("progress") or 0)
        now = time.monotonic()
        finished = status in {"done", "error", "cancelled"}
        if not finished and now - self.last_print < 0.25 and status == self.last_status:
            return
        self.last_print = now
        self.last_status = status
        line = f"[{progress * 100:3.0f}%] {label} {speed}".rstrip()
        if self.interactive:
            self.stream.write("\r" + line + "\x1b[K")
            if finished:
                self.stream.write("\n")
            self.stream.flush()
        elif finished or status == "running":
            self.stream.write(line + "\n")
            self.stream.flush()


def _settings(args: argparse.Namespace) -> Settings:
    settings = load_settings(getattr(args, "config", None))
    if getattr(args, "preset", None):
        from .transcode import preset_settings

        settings = preset_settings(settings, args.preset)
    overrides = {
        "bitrate": getattr(args, "bitrate", None),
        "mode": getattr(args, "mode", None),
        "sample_rate": getattr(args, "sample_rate", None),
        "channels": getattr(args, "channels", None),
        "speed": getattr(args, "speed", None),
        "cover": getattr(args, "covert", None),
        "layout": getattr(args, "layout", None),
        "client": getattr(args, "client", None),
        "audio_lang": getattr(args, "audio_lang", None),
        "output_dir": getattr(args, "output", None),
        "jobs": getattr(args, "jobs", None),
        "cookies_file": getattr(args, "cookies", None),
    }
    if getattr(args, "single", False):
        overrides["playlist"] = False
    return settings.with_overrides(**overrides)


def _expand_sources(sources: list[str]) -> list[str]:
    expanded: list[str] = []
    for source in sources:
        if source == "-":
            expanded.extend(line.strip() for line in sys.stdin if line.strip())
        else:
            expanded.append(source.strip())
    return expanded


def _dry_run(sources: list[str], settings: Settings, as_json: bool) -> int:
    from . import fetch
    from .naming import display_title
    from .probe import probe
    from .util import estimate_size

    rows: list[dict[str, Any]] = []
    for source in sources:
        if is_url(source):
            info = fetch.inspect(source, settings)
            if info.get("kind") == "playlist":
                entries = info.get("entries") or []
                total_duration = sum(float(entry.get("duration") or 0) for entry in entries)
                rows.append(
                    {
                        "source": source,
                        "kind": "playlist",
                        "title": info.get("title"),
                        "count": len(entries),
                        "duration": total_duration,
                        "estimate": estimate_size(total_duration, settings.bitrate),
                        "entries": entries,
                    }
                )
                if not as_json:
                    print(f"قائمة: {info.get('title')} — {len(entries)} مقطع")
                    for entry in entries:
                        size = estimate_size(entry.get("duration") or 0, settings.bitrate)
                        print(
                            f"  {entry['index']:>3}. {entry['title'][:70]}"
                            f"  [{format_duration(entry.get('duration'))}]"
                            f"  ≈ {human_size(size)}"
                        )
            else:
                duration = float(info.get("duration") or 0)
                rows.append(
                    {
                        "source": source,
                        "kind": "video",
                        "title": info.get("title"),
                        "count": 1,
                        "duration": duration,
                        "estimate": estimate_size(duration, settings.bitrate),
                        "entries": [],
                    }
                )
                if not as_json:
                    print(
                        f"فيديو: {display_title(info)} — {duration:.0f} ثانية "
                        f"— تقدير {human_size(estimate_size(duration, settings.bitrate))}"
                    )
        else:
            path = Path(source)
            media = probe(path)
            meta = {"title": media.tags.get("title") or path.stem}
            rows.append(
                {
                    "source": source,
                    "kind": "file",
                    "title": meta["title"],
                    "count": 1,
                    "duration": media.duration,
                    "estimate": estimate_size(media.duration, settings.bitrate),
                    "entries": [],
                }
            )
            if not as_json:
                print(
                    f"ملف: {path.name} — {media.duration:.0f} ثانية "
                    f"— تقدير {human_size(estimate_size(media.duration, settings.bitrate))}"
                )
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    from .jobs import JobManager

    sources = _expand_sources(args.sources)
    if not sources:
        print("لم تُدخل أي رابط أو ملف.", file=sys.stderr)
        return 2
    settings = _settings(args)
    settings.ensure_dirs()
    if args.dry_run:
        return _dry_run(sources, settings, args.json)

    manager = JobManager(settings, start_workers=False)
    pick = parse_pick(args.pick)
    jobs = manager.submit(
        sources,
        settings=settings,
        pick=pick,
        bundle=args.bundle,
        force=args.force,
        title=args.title,
    )
    if not args.json:
        manager.on_event = ConsoleProgress()
        print(f"المخرجات: {settings.output_path}")
        print(f"الجودة: MP3 {settings.bitrate}k · {settings.mode} · {settings.channels}ch")
    worst = 0
    results: list[dict[str, Any]] = []
    for job in jobs:
        manager.run_sync(job)
        final = manager.get(job.id)
        data = final.public() if final else job.public()
        results.append(data)
        if data["status"] == "error":
            worst = max(worst, 1)
        elif data["status"] == "cancelled":
            worst = max(worst, 2)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for data in results:
            print(f"\n{data['status']} — {data['title']}")
            if data.get("error"):
                print(f"  خطأ: {data['error']}", file=sys.stderr)
            for output in data.get("outputs") or []:
                print(f"  {output}  ({human_size(Path(output).stat().st_size)})")
            ia_url = (data.get("extra") or {}).get("ia_url")
            if ia_url:
                print(f"  Archive: {ia_url}")
    manager.stop()
    return worst


def cmd_probe(args: argparse.Namespace) -> int:
    return _dry_run(_expand_sources(args.sources), _settings(args), args.json)


def cmd_list(args: argparse.Namespace) -> int:
    from .jobs import JobManager

    settings = _settings(args)
    manager = JobManager(settings, start_workers=False)
    jobs = manager.search(args.search) if args.search else manager.list()
    if args.json:
        print(json.dumps(jobs, ensure_ascii=False, indent=2))
        return 0
    if not jobs:
        print("لا توجد مهام بعد.")
        return 0
    print(f"{'ID':<12} {'الحالة':<14} {'الحجم':>9}  العنوان")
    for job in jobs[: args.limit]:
        extra = " ⚠" if job.get("error") else ""
        print(
            f"{job['id']:<12} {job['status']:<14} "
            f"{human_size(job.get('size')):>9}  {str(job.get('title'))[:60]}{extra}"
        )
        for output in (job.get("outputs") or [])[:2]:
            print(f"    {output}")
    return 0


def cmd_rename(args: argparse.Namespace) -> int:
    from .jobs import JobManager

    manager = JobManager(_settings(args), start_workers=False)
    ok, message = manager.rename(args.job_id, " ".join(args.title))
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def cmd_rm(args: argparse.Namespace) -> int:
    from .jobs import JobManager

    manager = JobManager(_settings(args), start_workers=False)
    if args.all:
        count = manager.delete_many([job["id"] for job in manager.list(limit=100000)])
    elif args.finished:
        count = manager.clear_finished()
    elif args.ids:
        count = manager.delete_many(args.ids)
    else:
        print("حدّد ID أو استخدم --all / --finished", file=sys.stderr)
        return 2
    print(f"حُذفت {count} مهمة.")
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    from .jobs import JobManager

    settings = _settings(args)
    manager = JobManager(settings, start_workers=False)
    manager.on_event = ConsoleProgress()
    failures = 0
    for job_id in args.ids:
        job = manager.retry(job_id)
        if not job:
            print(f"{job_id}: غير موجودة", file=sys.stderr)
            failures += 1
            continue
        manager.run_sync(job)
        final = manager.get(job.id)
        if final and final.status != "done":
            failures += 1
        if final:
            print(f"\n{final.status} — {final.title}")
            for output in final.outputs:
                print(f"  {output}")
    return 1 if failures else 0


def cmd_cookies(args: argparse.Namespace) -> int:
    settings = load_settings(getattr(args, "config", None))
    target = settings.cookies_path or default_cookies_file()
    if args.action == "status":
        if not target.is_file():
            print("لا يوجد ملف كوكيز.")
            return 0
        lines = [
            line for line in target.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        print(f"الملف: {target}")
        print(f"الكوكيز: {len(lines)}")
        print(f"آخر تعديل: {time.ctime(target.stat().st_mtime)}")
        return 0
    if args.action == "rm":
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        print("حُذف ملف الكوكيز.")
        return 0
    if args.action in {"set", "paste"}:
        if args.action == "set":
            if not args.file:
                print("حدّد ملف cookies.txt", file=sys.stderr)
                return 2
            text = Path(args.file).read_text(encoding="utf-8", errors="replace")
        else:
            text = sys.stdin.read()
        meaningful = [
            line for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not meaningful or not any(line.split("\t").__len__() >= 6 for line in meaningful):
            print("لا يبدو ملف Netscape cookies.txt صالحاً.", file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.rstrip() + "\n", encoding="utf-8")
        os.chmod(target, 0o600)
        print(f"حُفظت {len(meaningful)} كوكي في: {target}")
        return 0
    return 2


def cmd_upload_ia(args: argparse.Namespace) -> int:
    from . import archive_org
    from .jobs import JobManager

    if args.access and args.secret:
        archive_org.save_credentials(args.access, args.secret)
        print("حُفظت بيانات Archive.org.")
    if not archive_org.credentials_status()["configured"]:
        print(
            "بيانات Archive.org غير مضبوطة: استخدم --access/--secret أو "
            "IA_ACCESS_KEY/IA_SECRET_KEY.",
            file=sys.stderr,
        )
        return 2
    manager = JobManager(_settings(args), start_workers=False)
    manager.on_event = ConsoleProgress()
    job = manager.submit_upload(args.ids, identifier=args.identifier, settings=_settings(args))
    manager.run_sync(job)
    final = manager.get(job.id)
    if not final:
        return 1
    if final.status == "done":
        print((final.extra or {}).get("ia_url", "تم الرفع."))
        return 0
    print(f"فشل الرفع: {final.error}", file=sys.stderr)
    return 1


def cmd_config(args: argparse.Namespace) -> int:
    settings = load_settings(getattr(args, "config", None))
    if args.set:
        key, _, value = args.set.partition("=")
        if not key:
            print("الصيغة: --set key=value", file=sys.stderr)
            return 2
        data = settings.to_dict()
        if key not in data:
            print(f"مفتاح غير معروف: {key}", file=sys.stderr)
            return 2
        current = data[key]
        if isinstance(current, bool):
            data[key] = value.lower() in {"1", "true", "yes", "on"}
        elif isinstance(current, int):
            data[key] = int(value)
        elif isinstance(current, float):
            data[key] = float(value)
        else:
            data[key] = value
        settings = Settings.from_dict(data)
        path = save_settings(settings, getattr(args, "config", None))
        print(f"حُفظ: {path}")
        return 0
    print(f"ملف الإعدادات: {config_file()}")
    print(json.dumps(settings.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import probe, transcode

    settings = load_settings(getattr(args, "config", None))
    checks: list[tuple[str, bool, str, bool]] = []

    def add(label: str, ok: bool, detail: str = "", critical: bool = False) -> None:
        checks.append((label, ok, detail, critical))

    add("Python", sys.version_info >= (3, 11), sys.version.split()[0], True)
    add("بيئة", True, "Termux" if os.environ.get("TERMUX_VERSION") else ("Replit" if os.environ.get("REPL_ID") else "Linux/أخرى"))

    ffmpeg = probe.ffmpeg_version()
    add("ffmpeg", bool(ffmpeg), ffmpeg or "غير مثبّت — pkg install ffmpeg", True)
    try:
        probe.ffprobe_path()
        add("ffprobe", True)
    except Exception as exc:
        add("ffprobe", False, str(exc), True)
    add("libmp3lame", probe.has_encoder("libmp3lame"), "مشفّر MP3", True)

    try:
        import yt_dlp

        add("yt-dlp", True, yt_dlp.version.__version__, True)
    except Exception as exc:
        add("yt-dlp", False, str(exc), True)

    deno = shutil.which("deno")
    if deno:
        try:
            version = subprocess.run(
                [deno, "--version"], capture_output=True, text=True, timeout=10
            ).stdout.splitlines()
            add("Deno (YouTube JS)", True, version[0] if version else deno)
        except Exception:
            add("Deno (YouTube JS)", True, deno)
    else:
        add("Deno (YouTube JS)", False, "غير مثبّت — بعض روابط YouTube ستحتاجه")

    node = shutil.which("node")
    add("Node (احتياطي)", bool(node), node or "غير مثبّت")

    try:
        settings.ensure_dirs()
        test = settings.output_path / ".ismp3-write-test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        add("مجلد المخرجات", True, str(settings.output_path), True)
    except Exception as exc:
        add("مجلد المخرجات", False, f"{settings.output_path}: {exc}", True)

    try:
        usage = shutil.disk_usage(settings.output_path)
        add("المساحة الحرة", usage.free > 50 * 1024 * 1024, human_size(usage.free))
    except Exception as exc:
        add("المساحة الحرة", False, str(exc))

    try:
        rate = transcode.validate(settings.bitrate, settings.sample_rate)
        add("تركيبة MP3", True, f"{settings.bitrate}k / {rate} Hz / {settings.channels}ch")
    except Exception as exc:
        add("تركيبة MP3", False, str(exc), True)

    cookies = settings.cookies_path
    add("Cookies", bool(cookies), str(cookies) if cookies else "غير مضافة (اختياري)")

    try:
        import importlib

        importlib.import_module("internetarchive")
        add("internetarchive", True, "رفع Archive.org متاح")
    except Exception:
        add("internetarchive", False, "اختياري — pip install 'ismp3[ia]'")

    critical_fail = False
    for label, ok, detail, critical in checks:
        symbol = "✅" if ok else ("❌" if critical else "⚠️ ")
        if not ok and critical:
            critical_fail = True
        print(f"{symbol} {label}" + (f": {detail}" if detail else ""))
    print(f"\nالمخرجات: {settings.output_path}")
    print(f"الإعدادات: {config_file()}")
    return 1 if critical_fail else 0


def cmd_update(args: argparse.Namespace) -> int:
    if getattr(args, "self_update", False):
        from . import updater

        ok, _updated, message = updater.pull()
        print(message)
        return 0 if ok else 1
    print("جارٍ تحديث yt-dlp…")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
        check=False,
    )
    if result.returncode == 0:
        try:
            import yt_dlp

            print(f"yt-dlp الآن: {yt_dlp.version.__version__}")
        except Exception:
            pass
    return result.returncode


def cmd_serve(args: argparse.Namespace) -> int:
    from . import web

    settings = load_settings(getattr(args, "config", None))
    return web.serve(
        settings,
        host=args.host or settings.host,
        port=args.port or settings.port,
        open_browser=not args.no_browser,
    )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="مسار ملف الإعدادات")

    parser = argparse.ArgumentParser(
        prog="ismp3",
        description="حوّل أي وسائط إلى MP3 بأقل حجم (24k افتراضياً) — واجهة عربية.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"ismp3 {__version__}")
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--config", default=argparse.SUPPRESS, help="مسار ملف الإعدادات")
        return p

    add = add_common(sub.add_parser("add", aliases=["a"], help="إضافة روابط أو ملفات", ))
    add.add_argument("sources", nargs="+", help="روابط، ملفات، مجلدات، أو - لقراءة stdin")
    add.add_argument("-b", "--bitrate", type=int, help=f"البتريت kbps (افتراضي 24). القيم: {BITRATES}")
    add.add_argument("--preset", choices=["tiny", "voice", "music", "hq"], help="إعداد جاهز")
    add.add_argument("--mode", choices=["cbr", "abr", "vbr"], help="نوع البتريت")
    add.add_argument("--sample-rate", type=int, default=None, help="0 = تلقائي")
    add.add_argument("--channels", type=int, choices=[1, 2], help="1 مونو (افتراضي) أو 2 ستيريو")
    add.add_argument("--speed", type=float, help="سرعة الصوت 0.5–4.0")
    add.add_argument("--cover", dest="covert", action="store_true", default=None, help="تضمين غلاف")
    add.add_argument("--no-cover", dest="covert", action="store_false", help="بلا غلاف (افتراضي)")
    add.add_argument("--layout", choices=LAYOUTS, help="تنظيم المجلدات")
    add.add_argument("--client", choices=CLIENTS, help="عميل YouTube للتشخيص")
    add.add_argument("--audio-lang", help="original أو رمز لغة مثل ar/en")
    add.add_argument("--single", action="store_true", help="تجاهل قائمة التشغيل")
    add.add_argument("--pick", help="اختيار مقاطع القائمة: 1,3,5-9")
    add.add_argument("--bundle", action="store_true", help="اجمع النتائج في ZIP")
    add.add_argument("--force", action="store_true", help="تجاهل سجل التنزيل السابق")
    add.add_argument("-o", "--output", help="مجلد المخرجات")
    add.add_argument("--jobs", type=int, help="عدد العمال (1–8)")
    add.add_argument("--cookies", help="مسار cookies.txt")
    add.add_argument("--title", help="عنوان المهمة")
    add.add_argument("--dry-run", action="store_true", help="معاينة بلا تنزيل")
    add.add_argument("--json", action="store_true", help="مخرجات JSON")
    add.set_defaults(func=cmd_add)

    probe_cmd = add_common(sub.add_parser("probe", help="معاينة رابط أو ملف", ))
    probe_cmd.add_argument("sources", nargs="+")
    probe_cmd.add_argument("--bitrate", type=int)
    probe_cmd.add_argument("--preset", choices=["tiny", "voice", "music", "hq"])
    probe_cmd.add_argument("--sample-rate", type=int, default=None)
    probe_cmd.add_argument("--single", action="store_true")
    probe_cmd.add_argument("--pick")
    probe_cmd.add_argument("--json", action="store_true")
    probe_cmd.set_defaults(func=cmd_probe)

    list_cmd = add_common(sub.add_parser("list", aliases=["ls"], help="عرض المهام", ))
    list_cmd.add_argument("--search", help="بحث")
    list_cmd.add_argument("--limit", type=int, default=50)
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=cmd_list)

    rename_cmd = add_common(sub.add_parser("rename", help="إعادة تسمية ملف", ))
    rename_cmd.add_argument("job_id")
    rename_cmd.add_argument("title", nargs="+")
    rename_cmd.set_defaults(func=cmd_rename)

    rm_cmd = add_common(sub.add_parser("rm", help="حذف مهام", ))
    rm_cmd.add_argument("ids", nargs="*")
    rm_cmd.add_argument("--all", action="store_true")
    rm_cmd.add_argument("--finished", action="store_true")
    rm_cmd.set_defaults(func=cmd_rm)

    retry_cmd = add_common(sub.add_parser("retry", help="إعادة محاولة", ))
    retry_cmd.add_argument("ids", nargs="+")
    retry_cmd.set_defaults(func=cmd_retry)

    cookies_cmd = add_common(sub.add_parser("cookies", help="إدارة cookies.txt", ))
    cookies_cmd.add_argument("action", choices=["status", "set", "paste", "rm"])
    cookies_cmd.add_argument("file", nargs="?")
    cookies_cmd.set_defaults(func=cmd_cookies)

    ia_cmd = add_common(sub.add_parser("upload-ia", help="رفع النتائج إلى Archive.org", ))
    ia_cmd.add_argument("ids", nargs="+")
    ia_cmd.add_argument("--identifier", default="")
    ia_cmd.add_argument("--access", default="")
    ia_cmd.add_argument("--secret", default="")
    ia_cmd.set_defaults(func=cmd_upload_ia)

    cfg_cmd = add_common(sub.add_parser("config", help="عرض/تعديل الإعدادات", ))
    cfg_cmd.add_argument("--set", help="key=value")
    cfg_cmd.set_defaults(func=cmd_config)

    doctor_cmd = add_common(sub.add_parser("doctor", help="فحص البيئة", ))
    doctor_cmd.set_defaults(func=cmd_doctor)

    update_cmd = add_common(sub.add_parser("update", help="تحديث yt-dlp أو التطبيق", ))
    update_cmd.add_argument(
        "--self",
        dest="self_update",
        action="store_true",
        help="تحديث نسخة ismp3 من Git (git pull)",
    )
    update_cmd.set_defaults(func=cmd_update)

    serve_cmd = add_common(sub.add_parser("serve", help="تشغيل الواجهة العربية", ))
    serve_cmd.add_argument("--host", default="")
    serve_cmd.add_argument("--port", type=int, default=0)
    serve_cmd.add_argument("--no-browser", action="store_true")
    serve_cmd.set_defaults(func=cmd_serve)

    sub.add_parser("version", help="الإصدار")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and not argv[0].startswith("-") and argv[0] not in COMMANDS:
        argv.insert(0, "add")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nأُلغي.", file=sys.stderr)
        return 130
    except Exception as exc:  # keep the CLI friendly; details with ISMP3_DEBUG=1
        if os.environ.get("ISMP3_DEBUG"):
            raise
        print(f"خطأ: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
