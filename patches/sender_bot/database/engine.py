import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

# ایمپورت مدل‌ها و کانفیگ
from database.models import Base, GlobalSettings, Category
from config import config

logger = logging.getLogger(__name__)

# ==========================================
# 1. DATABASE ENGINE & SESSION SETUP
# ==========================================
# 🏭 پچ ربات‌ساز (تله شماره ۲ — «Too many connections»):
# مقادیر pool از متغیرهای محیطی خوانده می‌شوند تا در حالت چند-اینستنسی روی
# MySQL مشترک، جمع کانکشن‌ها قابل کنترل باشد:
#   POOL_SIZE (پیش‌فرض 10) + MAX_OVERFLOW (پیش‌فرض 20) = حداکثر ۳۰ کانکشن برای هر اینستنس
# عدد قبلی (50+30=80) برای اجرای تک‌سروری خودتان مناسب بود؛ اما وقتی
# ۲۰+ اینستنس روی یک MySQL مشترک (max_connections=600) می‌نشینند،
# 20×80=1600 یعنی قفل شدن کل کارخانه!
# ⚠️ در فایل .env هر اینستنس، ربات‌ساز به‌صورت خودکار POOL_SIZE=10 می‌گذارد.
import os
_POOL_SIZE = int(os.getenv("POOL_SIZE", "10"))
_MAX_OVERFLOW = int(os.getenv("MAX_OVERFLOW", "20"))

engine = create_async_engine(
    config.MYSQL_URL,   # اصلاح نام متغیر بر اساس config.py
    echo=False, 
    pool_pre_ping=True,
    pool_size=_POOL_SIZE,       # 🏭 از env (پیش‌فرض 10 به جای 50)
    max_overflow=_MAX_OVERFLOW, # 🏭 از env (پیش‌فرض 20 به جای 30)
    pool_recycle=3600,  # بازیافت کانکشن‌ها هر ۱ ساعت برای جلوگیری از قطعی اتصال MySQL
    pool_timeout=30     # حداکثر زمان انتظار ورکرها برای دریافت کانکشن آزاد
)

async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

# ==========================================
# 2. INITIALIZE DATABASE (تزریق داده‌های پیش‌فرض)
# ==========================================
async def init_db() -> None:
    """
    بررسی و ساخت رکوردهای حیاتی دیتابیس در زمان استارت ربات.
    """
    try:
        # این دو خط باید از حالت کامنت خارج بشن تا جداول ساخته بشن 👇
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
        async with async_session() as session:
            
            # --- بخش الف: بررسی و ساخت تنظیمات پیش‌فرض سیستم ---
            stmt_settings = select(GlobalSettings).limit(1)
            result_settings = await session.execute(stmt_settings)
            settings = result_settings.scalar_one_or_none()
            
            if not settings:
                new_settings = GlobalSettings()
                session.add(new_settings)
                logger.info("✅ تنظیمات پیش‌فرض (GlobalSettings) در دیتابیس ایجاد شد.")
            
            # --- بخش ب: بررسی و ساخت دسته‌بندی پیش‌فرض ---
            stmt_cat = select(Category).where(Category.name == "default")
            result_cat = await session.execute(stmt_cat)
            default_cat = result_cat.scalar_one_or_none()
            
            if not default_cat:
                new_default = Category(name="default")
                session.add(new_default)
                logger.info("✅ پوشه 'default' با موفقیت ساخته شد.")
                
            # کامیت کردن تمام تغییرات
            await session.commit()
            
    except Exception as e:
        logger.error(f"❌ خطا در مقداردهی اولیه دیتابیس: {e}", exc_info=True)
        raise

# ==========================================
# 3. DEPENDENCY / HELPER
# ==========================================
async def get_db_session():
    """یک Generator برای استفاده در هندلرها جهت دریافت سشن دیتابیس"""
    async with async_session() as session:
        yield session
