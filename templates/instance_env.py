"""تولید فایل .env برای هر اینستنس — دقیقاً با نام متغیرهایی که config.py پروژه sender_bot می‌خواند."""


def render_instance_env(
    *,
    order_id: int,
    bot_token: str,
    api_id: int,
    api_hash: str,
    admin_id: int,
    db_name: str,
    db_user: str,
    db_pass: str,
    redis_host: str,
    redis_db: int,
    redis_user: str,
    redis_pass: str,
    fernet_key: str,
    force_join: str = "",
    login_proxy_url: str = "",
    video_link: str = "", 
    pool_size: int = 10,
    max_overflow: int = 20,
    max_accounts_per_proxy: int = 20,
    proxy_health_interval: int = 604800,
) -> str:
    """
    ⚠️ FERNET_KEY باید برای هر اینستنس یکتا باشد (تله شماره ۱):
    سشن‌های رمزشده هر مشتری فقط با کلید خودش باز می‌شود؛ فراموشی = از دست رفتن همه سشن‌ها.
    """
    return f"""# تولید خودکار توسط Bot Factory — سفارش #{order_id}
# ⚠️ محرمانه: حاوی توکن ربات، FERNET_KEY و رمز دیتابیس است. (chmod 600)

# --- Telegram ---
BOT_TOKEN={bot_token}
API_ID={api_id}
API_HASH={api_hash}
ADMIN_ID={admin_id}

# --- MySQL مشترک (کانتینر factory_mysql روی شبکه factory_net) ---
DB_USER={db_user}
DB_PASS={db_pass}
DB_HOST=factory_mysql
DB_PORT=3306
DB_NAME={db_name}

# --- Redis اختصاصی این اینستنس ---
REDIS_HOST={redis_host}
REDIS_PORT=6379
REDIS_DB={redis_db}
REDIS_USER={redis_user}
REDIS_PASS={redis_pass}

# --- رمزنگاری سشن (یکتا برای هر اینستنس) ---
FERNET_KEY={fernet_key}

# --- جوین اجباری ---
FORCE_JOIN_CHANNELS={force_join}

# --- پروکسی لاگین (fallback اولیه؛ استخر اصلی از جدول proxies همین دیتابیس سینک می‌شود) ---
LOGIN_PROXY_URL={login_proxy_url}

# --- تیونینگ اتصال (پچ engine.py — تله شماره ۲: جمع کانکشن‌ها) ---
POOL_SIZE={pool_size}
MAX_OVERFLOW={max_overflow}

# --- پروکسی ---
MAX_ACCOUNTS_PER_PROXY={max_accounts_per_proxy}
PROXY_HEALTH_CHECK_INTERVAL={proxy_health_interval}
"""
