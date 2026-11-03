#!/usr/bin/env bash
# Replit run command: install once if needed, then serve on $PORT.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1

if ! python -c "import yt_dlp" >/dev/null 2>&1; then
  echo "▶ تثبيت الاعتماديات لأول مرة…"
  python -m pip install -r requirements.txt
  python -m pip install -e .
fi

exec python -m ismp3 serve \
  --host 0.0.0.0 \
  --port "${PORT:-8787}" \
  --no-browser
