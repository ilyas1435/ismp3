#!/usr/bin/env bash
# Replit run command: install Python deps once, then serve on 0.0.0.0:$PORT.
# The port MUST match [[ports]].localPort in .replit (3000 by default).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
# Replit's Nix Python is sometimes marked "externally managed"; this makes
# the one-time pip install work instead of failing with PEP 668.
export PIP_BREAK_SYSTEM_PACKAGES=1
export ISMP3_CONFIG="${ISMP3_CONFIG:-$ROOT/data/config.toml}"

PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then
  echo "❌ لم أجد python3. أضف وحدة Python من Tools → Modules ثم أعد التشغيل." >&2
  exit 1
fi

echo "▶ Python: $("$PYTHON" --version 2>&1)"
echo "▶ المجلد: $ROOT"

check_media_tool() {
  name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "⚠️ $name غير مثبّت. تأكد من وجوده في replit.nix."
    return
  fi
  if output="$("$name" -version 2>&1)"; then
    echo "▶ $name: $(printf '%s\n' "$output" | sed -n '1p')"
  else
    echo "⚠️ $name موجود لكنه لا يعمل بسبب تعارض مكتبات Nix/GLIBC:"
    printf '%s\n' "$output" | sed -n '1,3p' | sed 's/^/   /'
    echo "   الحل: ثبّت [nix] channel = \"stable-24_05\" في .replit ثم أعد تحميل البيئة."
    echo "   من Shell: اكتب exit وافتح Shell جديداً — أو استخدم Stop ثم Run."
  fi
}
check_media_tool ffmpeg
check_media_tool ffprobe

if ! "$PYTHON" -c "import yt_dlp" >/dev/null 2>&1; then
  echo "▶ تثبيت اعتماديات Python (مرة واحدة فقط)…"
  "$PYTHON" -m pip install -r requirements.txt
fi

PORT="${PORT:-3000}"
echo "▶ ismp3 سيستمع على 0.0.0.0:$PORT"
echo "▶ إن ظهرت صفحة الخطأ، تأكد أن Networking يعرض المنفذ الداخلي $PORT → الخارجي 80."
exec "$PYTHON" -m ismp3 serve --host 0.0.0.0 --port "$PORT" --no-browser
