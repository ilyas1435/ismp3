"""تحديث نسخة Git من ismp3 دون فتح Shell.

يعمل على Replit عبر زر في الواجهة، ويمكن استدعاؤه من الطرفية:

    ismp3 update --self

لا يستخدم أي أوامر shell؛ كل شيء عبر subprocess بقائمة وسائط.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

TIMEOUT = 120


def repo_root() -> Path | None:
    """أعلى مجلد يحتوي على .git انطلاقاً من حزمة ismp3."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def update_supported() -> bool:
    """التحديث من الواجهة متاح على Replit (أو بتفعيل صريح)."""
    allowed = bool(os.environ.get("ISMP3_ALLOW_SELF_UPDATE")) or _on_replit()
    return allowed and repo_root() is not None


def _on_replit() -> bool:
    return bool(
        os.environ.get("REPL_ID")
        or os.environ.get("REPLIT_DEV_DOMAIN")
        or os.environ.get("REPLIT_DB_URL")
    )


def _run(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT,
        env=env,
        check=False,
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    return text[-600:] if text else f"git exit {result.returncode}"


def _branch(root: Path) -> str:
    result = _run(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = result.stdout.strip() if result.returncode == 0 else ""
    if branch and branch != "HEAD":
        return branch
    result = _run(root, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    remote = result.stdout.strip() if result.returncode == 0 else ""
    if remote.startswith("origin/"):
        return remote[len("origin/"):]
    return "main"


def pull(root: Path | None = None) -> tuple[bool, bool, str]:
    """يجلب آخر نسخة من origin ويعيد (نجاح، تغيّر، رسالة)."""
    root = root or repo_root()
    if root is None:
        return False, False, "هذه النسخة ليست مستنسخة Git؛ حدّثها يدوياً من GitHub."
    if not (root / ".git").exists():
        return False, False, "هذه النسخة ليست مستنسخة Git؛ حدّثها يدوياً من GitHub."

    try:
        before = _run(root, ["rev-parse", "--short", "HEAD"])
        if before.returncode != 0:
            return False, False, f"تعذّر قراءة إصدار Git: {_detail(before)}"
        old = before.stdout.strip()

        fetched = _run(root, ["fetch", "--prune", "origin"])
        if fetched.returncode != 0:
            return False, False, f"تعذّر جلب التحديث من GitHub: {_detail(fetched)}"

        branch = _branch(root)
        target = f"origin/{branch}"
        exists = _run(root, ["rev-parse", "--verify", "--quiet", target])
        if exists.returncode != 0:
            return False, False, f"الفرع {branch} غير موجود على origin."

        reset = _run(root, ["reset", "--hard", target])
        if reset.returncode != 0:
            return False, False, f"تعذّر تطبيق التحديث: {_detail(reset)}"

        after = _run(root, ["rev-parse", "--short", "HEAD"])
        new = after.stdout.strip() if after.returncode == 0 else "?"
        if old == new:
            return True, False, f"أنت على أحدث نسخة ({new})."
        return True, True, f"تم التحديث من {old} إلى {new}. أوقف التطبيق ثم شغّله من جديد."
    except FileNotFoundError:
        return False, False, "git غير مثبّت على هذا الخادم."
    except subprocess.TimeoutExpired:
        return False, False, "انتهت مهلة التحديث؛ تحقق من اتصال الشبكة."
    except OSError as exc:
        return False, False, f"تعذّر تشغيل git: {exc}"


__all__ = ["pull", "repo_root", "update_supported"]
