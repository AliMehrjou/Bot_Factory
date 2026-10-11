#!/usr/bin/env bash
# ==========================================
# 🚀 رُل‌اوت ایمیج جدید روی همه اینستنس‌ها
# ==========================================
# بعد از اجرای build_sender_image.sh، این اسکریپت ایمیج جدید را روی همه
# اینستنس‌های موجود اعمال می‌کند (force-recreate).
#
# چرا لازم است؟ کانتینر در لحظه ساخت به Image ID (نه تگ) قفل می‌شود؛ ریبیلد
# با تگ ثابت فقط اشاره‌گر تگ را جابجا می‌کند و کانتینرهای در حال اجرا همان
# نسخه قدیمی را اجرا می‌کنند. تنها راه، Recreate کردن کانتینرهاست.
#
# داده‌ها امن هستند: sessions/downloads/exports/banners همه bind-mount اند.
# راه دیگر (از داخل ربات): پنل ادمین → اینستنس‌ها → «🚀 آپدیت همه با ایمیج جدید»
#
# rollback:  docker tag sender_bot:YYYYMMDD sender_bot:v1  و دوباره همین اسکریپت
set -uo pipefail

FACTORY_DIR="${FACTORY_DIR:-/opt/factory}"

ok=0
fail=0
failed_list=()

echo "🚀 رُل‌اوت ایمیج روی همه اینستنس‌های $FACTORY_DIR/instances ..."

for comp in "$FACTORY_DIR"/instances/bot_*/compose.yml; do
  [[ -f "$comp" ]] || continue
  id="$(basename "$(dirname "$comp")" | cut -d_ -f2)"

  echo "────────────────────────────────────"
  echo "🔄 bot_$id — force-recreate ..."
  if docker compose -p "bot_$id" -f "$comp" up -d --force-recreate; then
    echo "✅ bot_$id بازسازی شد"
    ok=$((ok + 1))
  else
    echo "❌ bot_$id ناموفق"
    fail=$((fail + 1))
    failed_list+=("$id")
  fi
done

echo ""
echo "══════════════════════════════════════"
echo "🚀 رُل‌اوت تمام شد: $ok موفق / $fail ناموفق"
if [[ ${#failed_list[@]} -gt 0 ]]; then
  echo "❌ ناموفق‌ها: ${failed_list[*]}"
  echo "   لاگ بگیرید:  docker logs --tail 50 bot_<ID>"
fi
echo "💡 rollback: docker tag sender_bot:YYYYMMDD sender_bot:v1 و دوباره همین اسکریپت"
