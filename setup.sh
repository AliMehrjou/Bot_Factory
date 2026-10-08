#!/usr/bin/env bash
# ==========================================
# 🏭 Bot Factory — نصب خودکار سرور
# ==========================================
# اجرا به‌عنوان root:  bash setup.sh
# این اسکریپت (حدود ۱۵ دقیقه):
#   1) نصب Docker در صورت نبود
#   2) ساخت دایرکتوری‌های /opt/factory
#   3) بالا آوردن MySQL (max_connections=1000) + 4× Redis (ظرفیت ۶۰ اینستنس)
#   4) ساخت venv پایتون + نصب پکیج‌ها
#   5) سرویس systemd + اجرای ربات فروش
#   6) آماده‌سازی ساخت ایمیج sender_bot:v1
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
FACTORY_DIR="${FACTORY_DIR:-/opt/factory}"

# ---------- توابع کمکی ----------
msg()  { echo -e "\e[36m🏗 $*\e[0m"; }
ok()   { echo -e "\e[32m✅ $*\e[0m"; }
err()  { echo -e "\e[31m❌ $*\e[0m"; exit 1; }

[[ $EUID -eq 0 ]] || err "این اسکریپت باید با sudo/ root اجرا شود."

# ---------- 1) Docker ----------
if command -v docker &>/dev/null; then
  ok "Docker موجود است: $(docker --version)"
else
  msg "نصب Docker (در صورت خطای شبکه، پروکسی/VPN سرور را روشن کنید)..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
  ok "Docker نصب شد"
fi
docker compose version &>/dev/null || err "docker compose plugin موجود نیست — داکیومنت داکر را ببینید."

# ---------- 2) دایرکتوری‌ها ----------
msg "ساخت دایرکتوری‌ها در $FACTORY_DIR ..."
install -d -m 755 "$FACTORY_DIR" "$FACTORY_DIR/instances" "$FACTORY_DIR/backups" "$FACTORY_DIR/source"
ok "دایرکتوری‌ها آماده شد"

# ---------- 3) فایل .env ----------
if [[ ! -f "$ROOT_DIR/.env" ]]; then
  [[ -f "$ROOT_DIR/.env.example" ]] || err ".env.example پیدا نشد!"
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  chmod 600 "$ROOT_DIR/.env"
  err ".env ساخته شد ولی خالی است! آن را پر کنید (SALES_BOT_TOKEN, ADMIN_IDS, رمزها) و دوباره setup.sh را اجرا کنید."
fi
chmod 600 "$ROOT_DIR/.env"
ok ".env امن است (600)"

# ---------- 4) زیرساخت (MySQL + Redis) ----------
msg "اجرای MySQL و Redis (اولین بار pull ممکن است چند دقیقه طول بکشد)..."
docker compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ROOT_DIR/.env" up -d
ok "زیرساخت اجرا شد"

msg "صبر برای healthy شدن MySQL..."
for i in $(seq 1 60); do
  if docker exec factory_mysql mysqladmin ping --silent &>/dev/null; then
    ok "MySQL آماده است"; break
  fi
  [[ $i -eq 60 ]] && err "MySQL بعد از 60 تلاش بالا نیامد — لاگ: docker logs factory_mysql"
  sleep 5
done

# بررسی max_connections (تله شماره ۲ — باید با MYSQL_MAX_CONNECTIONS فایل .env یکی باشد)
MYSQL_ROOT_PASS=$(grep -E '^MYSQL_ROOT_PASS=' "$ROOT_DIR/.env" | cut -d= -f2-)
MAX_CONN=$(grep -E '^MYSQL_MAX_CONNECTIONS=' "$ROOT_DIR/.env" | cut -d= -f2- || echo 1000)
MAX_CONN=${MAX_CONN:-1000}

MX=$(docker exec factory_mysql mysql -uroot -p"$MYSQL_ROOT_PASS" -N -e "SELECT @@max_connections;" 2>/dev/null || echo 0)
if [[ "$MX" -lt "$MAX_CONN" ]]; then
  err "max_connections=$MX — فایل my.cnf مونت نشده یا کمتر از $MAX_CONN است! (نیاز به بازنگری docker-compose.yml)"
fi
ok "max_connections=$MX ✅ (حداقل $MAX_CONN)"

# ---------- 5) پایتون ----------
if ! command -v python3 &>/dev/null || [[ $(python3 -c 'import sys; print(sys.version_info >= (3, 11))') != "True" ]]; then
  msg "نصب پایتون 3.11+ ..."
  if ! apt-get update -qq && apt-get install -y -qq python3.11 python3.11-venv python3-pip 2>/dev/null; then
    echo -e "\e[33m⚠️ هشدار: پایتون 3.11 در مخازن یافت نشد! اسکریپت با پایتون پیش‌فرض سیستم (احتمالا 3.10) ادامه می‌دهد.\e[0m"
    apt-get install -y python3 python3-venv python3-pip
  fi
fi
PY=$(command -v python3.11 || command -v python3)

msg "ساخت venv و نصب پکیج‌ها..."
[[ -d "$ROOT_DIR/venv" ]] || "$PY" -m venv "$ROOT_DIR/venv"
"$ROOT_DIR/venv/bin/pip" install -q --upgrade pip
"$ROOT_DIR/venv/bin/pip" install -q -r "$ROOT_DIR/requirements.txt"
ok "پکیج‌ها نصب شدند"

# ---------- 6) systemd ----------
msg "نصب سرویس systemd..."
sed "s#__ROOT__#$ROOT_DIR#g" "$ROOT_DIR/systemd/bot-factory.service" > /etc/systemd/system/bot-factory.service
systemctl daemon-reload
systemctl enable --now bot-factory
sleep 5
if systemctl is-active --quiet bot-factory; then
  ok "ربات فروش اجرا شد (systemctl status bot-factory)"
else
  err "ربات فروش بالا نیامد — لاگ: journalctl -u bot-factory -n 50"
fi

# ---------- 7) ایمیج sender_bot ----------
if docker image inspect sender_bot:v1 &>/dev/null; then
  ok "ایمیج sender_bot:v1 موجود است"
else
  msg "ایمیج sender_bot:v1 هنوز ساخته نشده."
  echo "   سورس پروژه را در $FACTORY_DIR/source/sender_bot قرار دهید و اجرا کنید:"
  echo "     bash $ROOT_DIR/scripts/build_sender_image.sh"
  echo "   (تا قبل از آن، سفارش‌ها با خطای image not found شکست می‌خورند)"
fi

echo ""
echo "═══════════════════════════════════════════"
ok "🎉 نصب کامل شد!"
echo "  ▸ پنل ادمین: در ربات فروش /panel را بزنید"
echo "  ▸ ظرفیت پیش‌فرض: ۶۰ اسلات Redis و ۶۳ اینستنس MySQL — آماده ۵۰ سفارش همزمان"
echo "  ▸ مدیریت پروکسی: /proxies"
echo "  ▸ ساخت ایمیج: bash $ROOT_DIR/scripts/build_sender_image.sh"
echo "  ▸ بکاپ شبانه: crontab را با scripts/backup_factory.sh تنظیم کنید"
echo "═══════════════════════════════════════════"
