#!/usr/bin/env bash
# Termux setup for ismp3. Run: bash scripts/setup-termux.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ISMP3_VENV:-$HOME/.venvs/ismp3}"

echo "▶ تثبيت الحزم الأساسية…"
pkg update -y
pkg install -y python ffmpeg deno git

if command -v termux-setup-storage >/dev/null 2>&1; then
  echo "▶ طلب إذن التخزين (وافق على النافذة)…"
  termux-setup-storage || true
fi

echo "▶ إنشاء البيئة الافتراضية في $VENV …"
if [ ! -x "$VENV/bin/python" ]; then
  python -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

python -m pip install --upgrade pip
python -m pip install -e "${ROOT}[ia]"

echo
echo "✅ تم التثبيت."
echo "   فعّل البيئة:  source $VENV/bin/activate"
echo "   افحص:         ismp3 doctor"
echo "   شغّل الواجهة: ismp3 serve"
