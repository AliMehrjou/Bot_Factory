"""
انجین‌های دیتابیس فکتوری:
- engine / async_session     → دیتابیس مرکزی bot_factory
- get_root_engine()          → اتصال root برای CREATE DATABASE / GRANT (ساخت اینستنس)
- get_shared_app_engine()    → انجین واحد اپلیکیشن برای دسترسی کراس‌دیتابیس (سینک جدول proxies)
همه اتصال‌ها asyncmy هستند و escaping واقعی SQL انجام می‌شود (ضد تزریق).
"""
import logging
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config import config

logger = logging.getLogger(__name__)

engine = create_async_engine(
    f"mysql+asyncmy://{quote_plus(config.FACTORY_APP_USER)}:{quote_plus(config.FACTORY_APP_PASS)}"
    f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}",
    echo=False, pool_pre_ping=True, pool_size=10, max_overflow=10, pool_recycle=3600,
)

async_session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

_root_engine = None
_shared_app_engine = None


def get_root_engine():
    """انجین root — فقط برای CREATE DATABASE و GRANT استفاده می‌شود."""
    global _root_engine
    if _root_engine is None:
        _root_engine = create_async_engine(
            f"mysql+asyncmy://root:{quote_plus(config.MYSQL_ROOT_PASS)}"
            f"@{config.DB_HOST}:{config.DB_PORT}",
            echo=False, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=1800,
            isolation_level="AUTOCOMMIT",
        )
    return _root_engine


def get_shared_app_engine():
    """انجین واحد اپلیکیشن فکتوری برای دسترسی کراس‌دیتابیس (مانند سینک پروکسی) بدون کش و اتصال اضافی."""
    global _shared_app_engine
    if _shared_app_engine is None:
        _shared_app_engine = create_async_engine(
            f"mysql+asyncmy://{quote_plus(config.FACTORY_APP_USER)}:{quote_plus(config.FACTORY_APP_PASS)}"
            f"@{config.DB_HOST}:{config.DB_PORT}",
            echo=False, pool_pre_ping=True, pool_size=5, max_overflow=10, pool_recycle=1800,
        )
    return _shared_app_engine


async def drop_instance_engine(db_name: str) -> None:
    # با توجه به حذف کش انجین‌های مجزا، این تابع صرفاً برای سازگاری رابط باقی می‌ماند و کار خاصی نمی‌کند.
    pass


# بازنویسی کامل
async def init_db() -> None:
    """ساخت جداول فکتوری در اولین اجرا (الگوی همان sender_bot)."""
    from database.models import Base, RedisSlot
    from sqlalchemy import select, func, text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # مایگریشن برای اصلاح نوع ستون‌های عددی به BigInteger در صورت نیاز (Idempotent)
        db_name = config.DB_NAME
        check_sql = text(
            "SELECT DATA_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = 'orders' AND COLUMN_NAME = 'tg_id'"
        )
        res = await conn.execute(check_sql, {"db": db_name})
        row = res.fetchone()
        if row and row[0].lower() == 'int':
            logger.info("Migrating table columns to BIGINT...")
            await conn.execute(text(
                "ALTER TABLE orders "
                "MODIFY tg_id BIGINT NOT NULL, "
                "MODIFY api_id BIGINT NOT NULL, "
                "MODIFY admin_id BIGINT NOT NULL"
            ))

    # پر کردن اسلات‌های redis بر اساس NUM_REDIS (idempotent)
    async with async_session() as session:
        total = await session.scalar(select(func.count(RedisSlot.slot_id))) or 0
        capacity = config.redis_capacity
        if total < capacity:
            for slot in range(capacity):
                exists = await session.get(RedisSlot, slot)
                if not exists:
                    session.add(RedisSlot(slot_id=slot))
            await session.commit()
            logger.info(f"✅ {capacity} اسلات redis آماده شد ({config.NUM_REDIS} کانتینر × {config.REDIS_DBS_PER}).")



async def close_all() -> None:
    global _root_engine, _shared_app_engine
    await engine.dispose()
    if _shared_app_engine is not None:
        await _shared_app_engine.dispose()
        _shared_app_engine = None
    if _root_engine is not None:
        await _root_engine.dispose()
        _root_engine = None
