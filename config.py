"""
🤖 Bot Factory — تنظیمات مرکزی
================================
ربات‌ساز (کارخانه ساخت ربات) — هسته فکتوری.
همه مقادیر از فایل .env خوانده می‌شوند تا هر سرور بدون تغییر کد قابل استقرار باشد.

⚠️ امنیت: فایل .env حاوی رمز root مای‌اس‌کیوال و توکن ربات فروش است؛
   حتماً دسترسی 600 (rw-------) روی آن بگذارید (setup.sh خودش انجام می‌دهد).
"""
import os
import json
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on", "y")


# پلن‌های فروش — قابل ویرایش مستقیم یا از env به صورت JSON
# ⚠️ قیمت‌ها به «سنت» (واحد کوچک دلار) ذخیره می‌شوند تا ستون‌های Integer دیتابیس دست‌نخورده بمانند:
#    ۳.۵ دلار = 350 | ۶.۵ دلار = 650 | ۹ دلار = 900 | ۱۷ دلار = 1700
#    در نمایش به کاربر، خودکار به دلار تبدیل می‌شود (تابع fmt_price).
_DEFAULT_PLANS = {
    "monthly":    {"title": "یک‌ماهه",  "price": 350,  "days": 30,  "desc": "ربات سندر اختصاصی یک‌ماهه"},
    "bimonthly":  {"title": "دوماهه",  "price": 650,  "days": 60,  "desc": "ربات سندر اختصاصی دوماهه"},
    "quarterly":  {"title": "سه‌ماهه",  "price": 900,  "days": 90,  "desc": "ربات سندر اختصاصی سه‌ماهه"},
    "semiannual": {"title": "شش‌ماهه", "price": 1700, "days": 180, "desc": "ربات سندر اختصاصی شش‌ماهه"},
}


def fmt_price(amount) -> str:
    """
    تبدیل سنت به نمایش دلاری:
    350 → «3.5» | 650 → «6.5» | 900 → «9» | 1700 → «17» | 475 → «4.75»
    """
    v = float(amount) / 100.0
    if v.is_integer():
        return f"{int(v):,}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def parse_to_cents(raw: str):
    """
    ورودی متنی کاربر/ادمین به سنت: «3.5» یا «3/5» یا «10» → 350/350/1000
    نامعتبر → None  (فقط عدد و یک جداکننده اعشار مجاز است)
    """
    s = (raw or "").strip().replace("/", ".").replace(",", ".")
    if not s or s.count(".") > 1 or not all(c.isdigit() or c == "." for c in s):
        return None
    try:
        cents = round(float(s) * 100)
    except (ValueError, OverflowError):
        return None
    return cents if cents > 0 else None

def _load_plans() -> dict:
    raw = os.getenv("PLANS_JSON", "")
    if raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            pass
    return _DEFAULT_PLANS


@dataclass
class FactoryConfig:
    # ---------- ربات فروش (aiogram) ----------
    SALES_BOT_TOKEN: str = os.getenv("SALES_BOT_TOKEN", "")
    ADMIN_IDS: list = field(default_factory=lambda: [
        int(x) for x in os.getenv("ADMIN_IDS", "6722340162").replace(" ", "").split(",") if x.strip()
    ])

    # ---------- پشتیبانی ----------
    SUPPORT_USERNAME: str = os.getenv("SUPPORT_USERNAME", "@support_admin")
    MAX_PENDING_ORDERS: int = _int("MAX_PENDING_ORDERS", 3)

    # ---------- 🛒 ربات خرید شماره مجازی ----------
    # لینک/یوزرنیم ربات خرید شماره مجازی (تیم Plus Number) — دکمه شیشه‌ای صفحه «نیاز به اکانت تلگرامی؟»
    # مثال: @PlusNumberBot  یا  https://t.me/PlusNumberBot
    VIRTUAL_NUMBER_BOT: str = os.getenv("VIRTUAL_NUMBER_BOT", "@PlusNumberBot")

    # ---------- 🎬 ویدیوی آموزشی توکن ----------
    # file_id تلگرام ویدیوی آموزشی (fallback اولیه از .env).
    # راه بهتر: پنل ادمین → «🎬 ویدیوی آموزشی» → آپلود مستقیم ویدیو؛
    # فایل‌آیدی در دیتابیس ذخیره شده و از آن لحظه خودکار همراه متن‌های راهنما ارسال می‌شود.
    # اگر هیچ‌کدام تنظیم نباشد، متن‌ها بدون ویدیو ارسال می‌شوند (بدون خطا).
    # پیشوند نوع اختیاری است:  animation:<file_id>  یا  document:<file_id>  (پیش‌فرض: video)
    TOKEN_GUIDE_VIDEO_FILE_ID: str = os.getenv("TOKEN_GUIDE_VIDEO_FILE_ID", "")

    VIDEO_LINK: str = os.getenv("VIDEO_LINK", "")
    # ---------- 🔧 حالت ساخت دستی ----------
    # true  = سفارش‌های پرداخت‌شده به‌جای دیپلوی خودکار، به پشتیبانی ارجاع می‌شوند
    #         (پشتیبانی ایمیج ربات را می‌سازد و از پنل وضعیت را به‌روز می‌کند)
    # false = رفتار قدیمی: پرداخت از کیف پول → ساخت خودکار فوری
    MANUAL_BUILD_MODE: bool = _flag("MANUAL_BUILD_MODE", "true")

    # ---------- دیتابیس مرکزی فکتوری ----------
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT: int = _int("DB_PORT", 3306)
    DB_NAME: str = os.getenv("DB_NAME", "bot_factory")
    MYSQL_ROOT_PASS: str = os.getenv("MYSQL_ROOT_PASS", "")
    # ⚠️ باید با max_connections فایل my.cnf یکی باشد (سقف اینستنس‌ها از همین حساب می‌شود)
    MYSQL_MAX_CONNECTIONS: int = _int("MYSQL_MAX_CONNECTIONS", 1000)
    # کانکشن‌های رزروشده برای خود فکتوری (ربات فروش + سینک پروکسی)
    MYSQL_RESERVE_CONN: int = _int("MYSQL_RESERVE_CONN", 50)

    # کاربر اپلیکیشن که به اینستنس‌ها تزریق می‌شود (DB_USER داخل .env اینستنس)
    FACTORY_APP_USER: str = os.getenv("FACTORY_APP_USER", "factory_app")
    FACTORY_APP_PASS: str = os.getenv("FACTORY_APP_PASS", "")

    # ---------- Redis (ظرفیت هر کانتینر = 15 دیتابیس منطقی) ----------
    REDIS_PASS: str = os.getenv("REDIS_PASS", "")
    NUM_REDIS: int = max(1, _int("NUM_REDIS", 4))
    # هر دیتابیس منطقی redis جدا از دیگری است؛ نقشه «اسلات» در جدول redis_slots نگه داشته می‌شود
    # ظرفیت کل = NUM_REDIS × 15  (پیش‌فرض ۴ کانتینر = ۶۰ اسلات → آماده ۵۰ سفارش همزمان)
    REDIS_DBS_PER = 15  # دیتابیس‌های منطقی 0..14 هر کانتینر

    # ---------- مسیرها و ایمیج ----------
    FACTORY_DIR: str = os.getenv("FACTORY_DIR", "/opt/factory")
    BOT_IMAGE: str = os.getenv("BOT_IMAGE", "sender_bot:v1")

    # ---------- پرداخت و واحد پول ----------
    PAYMENT_CARD: str = os.getenv("PAYMENT_CARD", "6037-0000-0000-0000")
    PAYMENT_CARD_HOLDER: str = os.getenv("PAYMENT_CARD_HOLDER", "")
    PAYMENT_NOTE: str = os.getenv("PAYMENT_NOTE", "بعد از واریز، تصویر رسید را همینجا بفرستید.")
    # واحد پول نمایشی قیمت‌ها/کیف پول (قیمت‌ها همیشه به سنت ذخیره می‌شوند)
    CURRENCY: str = os.getenv("CURRENCY", "دلار")

    # پروکسی HTTP خروجی برای اتصال به تلگرام (مثل http://127.0.0.1:10809) — خالی = اتصال مستقیم
    OUTBOUND_PROXY: str = os.getenv("OUTBOUND_PROXY", "")

    # ---------- پروکسی ----------
    PROXIES_PER_INSTANCE: int = _int("PROXIES_PER_INSTANCE", 10)       # پروکسی تخصیص‌یافته به هر اینستنس (0 = کل استخر)
    PROXY_CHECK_INTERVAL: int = _int("PROXY_CHECK_INTERVAL", 180)      # فاصله تست مداوم (ثانیه)
    PROXY_CHECK_TIMEOUT: int = _int("PROXY_CHECK_TIMEOUT", 8)          # تایم‌اوت هر تست
    PROXY_DEAD_FAILS: int = _int("PROXY_DEAD_FAILS", 3)                # شکست پیاپی تا علامت DEAD
    PROXY_RECOVER_OKS: int = _int("PROXY_RECOVER_OKS", 2)              # موفقیت پیاپی برای بازگشت
    PROXY_WEAK_MS: int = _int("PROXY_WEAK_MS", 1200)                   # بالاتر از این = WEAK
    PROXY_CONCURRENCY: int = _int("PROXY_CONCURRENCY", 8)              # تست همزمان
    PROXY_USAGE_TYPE: str = os.getenv("PROXY_USAGE_TYPE", "login")     # login | send | both
    # هدف تست: DC2 تلگرام — همان مسیری که Pyrofork برای MTProto استفاده می‌کند
    PROXY_TEST_HOST: str = os.getenv("PROXY_TEST_HOST", "149.154.167.50")
    PROXY_TEST_PORT: int = _int("PROXY_TEST_PORT", 443)
    PROXY_NOTIFY_HEARTBEAT: int = _int("PROXY_NOTIFY_HEARTBEAT", 20)   # هر چند سیکل گزارش سلامت بدهد

    # ---------- 🎯 تخصیص پروکسی به هر اینستنس (مدیریت تفکیک‌شده ادمین) ----------
    # موقع provision اینستنس جدید، به‌صورت خودکار بهترین پروکسی‌های استخر به آن
    # تخصیص می‌یابند (لاگین + سندر) تا مشتری هیچ دخالتی نداشته باشد.
    AUTO_ASSIGN_ON_PROVISION: bool = _flag("AUTO_ASSIGN_ON_PROVISION", "true")
    # تعداد پروکسی لاگین که خودکار به اینستنس جدید تخصیص می‌یابد (0 = بدون تخصیص خودکار لاگین)
    AUTO_ASSIGN_LOGIN_COUNT: int = _int("AUTO_ASSIGN_LOGIN_COUNT", 10)
    # تعداد پروکسی سندر که خودکار به اینستنس جدید تخصیص می‌یابد (0 = بدون تخصیص خودکار سندر)
    AUTO_ASSIGN_SENDER_COUNT: int = _int("AUTO_ASSIGN_SENDER_COUNT", 10)
    # سقف تعداد خط پروکسی که ادمین در یک پیام برای یک اینستنس می‌فرستد
    INSTANCE_PROXY_MAX_LINES: int = _int("INSTANCE_PROXY_MAX_LINES", 300)
    # هشدار کمبود: اگر سهمیه سالم یک اینستنس از این عدد کمتر شود ادمین هشدار می‌گیرد
    INSTANCE_PROXY_MIN_HEALTHY: int = _int("INSTANCE_PROXY_MIN_HEALTHY", 3)

    # ---------- منابع اینستنس‌ها (تله شماره ۴: محدودسازی منابع) ----------
    # برای ۵۰ اینستنس همزمان: 50×512m = 25.6GB سقف (سرور پیشنهادی: 32GB RAM / 8vCPU)
    INSTANCE_MEM_LIMIT: str = os.getenv("INSTANCE_MEM_LIMIT", "512m")
    INSTANCE_CPUS: str = os.getenv("INSTANCE_CPUS", "0.5")
    INSTANCE_PIDS_LIMIT: int = _int("INSTANCE_PIDS_LIMIT", 200)
    INSTANCE_LOG_MAX_SIZE: str = os.getenv("INSTANCE_LOG_MAX_SIZE", "20m")

    # پول اتصال هر اینستنس به MySQL مشترک (تله شماره ۲ — جمع کانکشن‌ها):
    # هر اینستنس حداکثر POOL+OVERFLOW کانکشن می‌خواهد؛ سقف اینستنس‌های فعال:
    #   (MYSQL_MAX_CONNECTIONS - MYSQL_RESERVE_CONN) // (POOL + OVERFLOW)
    # پیش‌فرض: (1000-50)//(5+10) = 63 اینستنس → آماده ۵۰ سفارش همزمان
    INSTANCE_DB_POOL_SIZE: int = max(1, _int("INSTANCE_DB_POOL_SIZE", 5))
    INSTANCE_DB_MAX_OVERFLOW: int = max(0, _int("INSTANCE_DB_MAX_OVERFLOW", 10))

    # ---------- دیپلوی ----------
    DEPLOY_STARTUP_TIMEOUT: int = _int("DEPLOY_STARTUP_TIMEOUT", 120)  # مهلت ظهور مارکر استارتاپ
    INSTANCE_STARTUP_MARKER: str = os.getenv("INSTANCE_STARTUP_MARKER", "Master Control Panel")
    # صف دیپلوی — چندکارگره برای هضم موج سفارش (۵۰ سفارش: ~۲۵ دقیقه با ۲ کارگر)
    DEPLOY_QUEUE_WORKERS: int = max(1, _int("DEPLOY_QUEUE_WORKERS", 2))
    DEPLOY_QUEUE_SIZE: int = max(10, _int("DEPLOY_QUEUE_SIZE", 200))

    # ---------- انقضای اشتراک ----------
    EXPIRY_AUTO_STOP: bool = _flag("EXPIRY_AUTO_STOP", "false")
    EXPIRY_WARN_DAYS: int = _int("EXPIRY_WARN_DAYS", 3)

    # ---------- پلن‌ها ----------
    PLANS: dict = field(default_factory=_load_plans)

    # ---------------- helper ----------------
    @property
    def instances_dir(self) -> str:
        return os.path.join(self.FACTORY_DIR, "instances")

    @property
    def backups_dir(self) -> str:
        return os.path.join(self.FACTORY_DIR, "backups")

    def instance_dir(self, order_id: int) -> str:
        return os.path.join(self.instances_dir, f"bot_{int(order_id)}")

    def db_name_for(self, order_id: int) -> str:
        return f"sender_{int(order_id)}"

    def redis_for_slot(self, slot: int) -> tuple:
        """اسلات -> (کانتینر redis، شماره db منطقی). ظرفیت کل = NUM_REDIS × REDIS_DBS_PER"""
        container_idx = slot // self.REDIS_DBS_PER
        if container_idx >= self.NUM_REDIS:
            raise ValueError(
                f"خطای ظرفیت! اسلات {slot} خارج از ظرفیت است؛ NUM_REDIS را در .env افزایش دهید "
                f"(ظرفیت فعلی: {self.redis_capacity} اینستنس)"
            )
        return f"factory_redis_{container_idx + 1}", slot % self.REDIS_DBS_PER

    @property
    def redis_capacity(self) -> int:
        return self.NUM_REDIS * self.REDIS_DBS_PER

    @property
    def max_instances_by_mysql(self) -> int:
        """سقف اینستنس‌های فعال بر اساس کانکشن‌های MySQL (منبع واحد محاسبه گارد provision)."""
        return (self.MYSQL_MAX_CONNECTIONS - self.MYSQL_RESERVE_CONN) // (
            self.INSTANCE_DB_POOL_SIZE + self.INSTANCE_DB_MAX_OVERFLOW
        )


config = FactoryConfig()

# اعتبارسنجی اولیه — خطای واضح فارسی به جای کرش گیج‌کننده
if not config.SALES_BOT_TOKEN:
    raise RuntimeError("SALES_BOT_TOKEN در .env تنظیم نشده است.")
if not config.ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS در .env تنظیم نشده است (شماره‌ها با کاما جدا شوند).")
if not config.MYSQL_ROOT_PASS:
    raise RuntimeError("MYSQL_ROOT_PASS در .env تنظیم نشده است.")
if not config.FACTORY_APP_PASS:
    raise RuntimeError("FACTORY_APP_PASS در .env تنظیم نشده است.")