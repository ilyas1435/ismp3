#!/usr/bin/env bash
# Replit entrypoint with auto-restart after an in-app update.
#
# The web server exits with code 75 only when it has just pulled a new revision.
# Normal stops (Ctrl-C / Replit Stop) exit with 0/143 and must not restart.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export ISMP3_AUTO_RESTART=1

while true; do
  bash "$ROOT/scripts/run-replit.sh"
  code=$?
  if [ "$code" -ne 75 ]; then
    exit "$code"
  fi
  echo "♻️  تم التحديث — إعادة تشغيل ismp3 تلقائياً…"
  sleep 1
done
