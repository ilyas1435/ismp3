#!/usr/bin/env bash
# One-time setup for a Replit Repl imported from GitHub.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_BREAK_SYSTEM_PACKAGES=1

PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then
  echo "❌ لم أجد python3. أضف وحدة Python من Tools → Modules." >&2
  exit 1
fi

echo "▶ Python: $("$PYTHON" --version 2>&1)"
echo "▶ تثبيت متطلبات Python…"
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" -m pip install -e .

echo "▶ فحص البيئة…"
"$PYTHON" -m ismp3 doctor || true

echo
echo "✅ جاهز. اضغط Run أو نفّذ:"
echo "   bash scripts/run-replit.sh"
