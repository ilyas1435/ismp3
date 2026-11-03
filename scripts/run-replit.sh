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

if ! "$PYTHON" -c "import yt_dlp" >/dev/null 2>&1; then
  echo "▶ تثبيت اعتماديات Python (مرة واحدة فقط)…"
  "$PYTHON" -m pip install -r requirements.txt
fi

PORT="${PORT:-3000}"
echo "▶ ismp3 سيستمع على 0.0.0.0:$PORT"
echo "▶ إن ظهرت صفحة الخطأ، تأكد أن Networking يعرض المنفذ الداخلي $PORT → الخارجي 80."
exec "$PYTHON" -m ismp3 serve --host 0.0.0.0 --port "$PORT" --no-browser
