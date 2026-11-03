#!/usr/bin/env bash
# One-time setup for a Replit Repl imported from GitHub.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

echo "▶ تثبيت متطلبات Python…"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .

echo "▶ فحص البيئة…"
python -m ismp3 doctor || true

echo
echo "✅ جاهز. اضغط Run أو نفّذ:"
echo "   bash scripts/run-replit.sh"
