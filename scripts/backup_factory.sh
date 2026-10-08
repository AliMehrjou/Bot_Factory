#!/usr/bin/env bash
# ==========================================
# 💾 بکاپ شبانه ربات‌ساز
# ==========================================
# همه چیزِ غیرقابل بازیابی: .env اینستنس‌ها (FERNET_KEY!) + دیتابیس فکتوری
# + لیست پروکسی‌ها + دیتابیس‌های مشتری (اختیاری)
#
# crontab -e  (هر شب ۳ بامداد):
#   0 3 * * * /bin/bash /opt/bot_factory/scripts/backup_factory.sh >> /var/log/factory_backup.log 2>&1
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT_DIR/.env" 2>/dev/null || { echo ".env پیدا نشد"; exit 1; }
FACTORY_DIR="${FACTORY_DIR:-/opt/factory}"
BACKUPS="$FACTORY_DIR/backups"
STAMP=$(date +%Y%m%d-%H%M)
OUT="$BACKUPS/factory-backup-$STAMP.tar.gz"

mkdir -p "$BACKUPS"

echo "[$(date)] 💾 شروع بکاپ..."

# 1) mysqldump دیتابیس فکتوری (سفارش‌ها + کلیدهای FERNET + لیست پروکسی)
if ! docker exec factory_mysql sh -c "mysqldump -uroot -p\"$MYSQL_ROOT_PASS\" --databases ${DB_NAME:-bot_factory}" > "$BACKUPS/factory_db_$STAMP.sql" 2>>"$BACKUPS/backup_error.log"; then
  echo "❌ خطا در mysqldump! اسکریپت متوقف شد. لطفا $BACKUPS/backup_error.log را بررسی کنید."
  rm -f "$BACKUPS/factory_db_$STAMP.sql"
  exit 1
fi

# 2) (اختیاری) دیتابیس‌های مشتری — این خط را فعال کنید اگر می‌خواهید:
# docker exec factory_mysql sh -c \
#   "mysqldump -uroot -p\"$MYSQL_ROOT_PASS\" --databases \$(mysql -uroot -p\"$MYSQL_ROOT_PASS\" -N -e \"SHOW DATABASES LIKE 'sender_%'\")" \
#   2>>"$BACKUPS/backup_error.log" > "$BACKUPS/instances_db_$STAMP.sql"

# 3) بسته‌بندی: .env همه اینستنس‌ها + سورس SQL (حذف دایرکتوری‌های حجیم غیرضروری)
tar -czf "$OUT" \
  --exclude='instances/*/downloads' \
  --exclude='instances/*/exports' \
  --exclude='instances/*/banners' \
  --exclude='instances/*/sessions' \
  --exclude='instances/*/profile_photos' \
  -C "$FACTORY_DIR" instances \
  -C "$BACKUPS" "factory_db_$STAMP.sql" 2>/dev/null || \
  tar -czf "$OUT" -C "$BACKUPS" "factory_db_$STAMP.sql"

rm -f "$BACKUPS/factory_db_$STAMP.sql"
chmod 600 "$OUT"

# 4) نگه‌داشتن فقط ۳۰ بکاپ آخر
ls -1t "$BACKUPS"/factory-backup-*.tar.gz 2>/dev/null | tail -n +31 | xargs -r rm -f

echo "[$(date)] ✅ بکاپ: $OUT ($(du -h "$OUT" | cut -f1))"
echo "   ⚠️ هر از گاهی یک نسخه را خارج از سرور (off-site) کپی کنید!"
