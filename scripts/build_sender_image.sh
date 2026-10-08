#!/usr/bin/env bash
# ==========================================
# 📦 ساخت ایمیج sender_bot:v1 (یک بار برای همیشه)
# ==========================================
# ورودی: سورس پروژه Bulk Sender در /opt/factory/source/sender_bot
# خروجی: ایمیج sender_bot:v1 — همه اینستنس‌ها از همین ایمیج اجرا می‌شوند
# (روش ایمیج مشترک: دیپلوی ~۱۰ ثانیه، دیسک ~۱۰× کمتر، بدون pull در لحظه سفارش)
#
# مراحل:
#   1) اعمال پچ‌های سازگاری (engine.py با pool قابل تنظیم)
#   2) docker build
#   3) تگ تاریخ برای rollback
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FACTORY_DIR="${FACTORY_DIR:-/opt/factory}"
SRC="${1:-$FACTORY_DIR/source/sender_bot}"

[[ -f "$SRC/Dockerfile" ]] || { echo "❌ Dockerfile در $SRC پیدا نشد"; exit 1; }
[[ -f "$SRC/main.py" ]]    || { echo "❌ سورس پروژه در $SRC کامل نیست"; exit 1; }

echo "🏗 ساخت ایمیج از: $SRC"

# --- 1) اعمال پچ (کپی امن با بکاپ از نسخه اصلی) ---
PATCH_FILES=(
  "database/engine.py"
  "config.py"
  "main.py"
  "workers/sender.py"
)

for FILE in "${PATCH_FILES[@]}"; do
  PATCH_SRC="$ROOT_DIR/patches/sender_bot/$FILE"
  if [[ -f "$PATCH_SRC" ]]; then
    TARGET_DIR="$SRC/$(dirname "$FILE")"
    mkdir -p "$TARGET_DIR/_original"
    if [[ -f "$SRC/$FILE" && ! -f "$TARGET_DIR/_original/$(basename "$FILE")" ]]; then
      cp "$SRC/$FILE" "$TARGET_DIR/_original/$(basename "$FILE")"
    fi
    cp "$PATCH_SRC" "$SRC/$FILE"
    echo "✅ پچ $FILE اعمال شد"
  fi
done
# --- 2) build ---
docker build -t sender_bot:v1 "$SRC"

# --- 3) تگ تاریخ ---
docker tag sender_bot:v1 "sender_bot:$(date +%Y%m%d)"

echo ""
echo "✅ ایمیج آماده است:"
docker images sender_bot --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
echo ""
echo "💡 rollback به نسخه قبل: docker tag sender_bot:YYYYMMDD sender_bot:v1"
