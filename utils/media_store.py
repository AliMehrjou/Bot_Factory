"""
🎬 مخزن رسانه‌های آموزشی ربات (نسخه ۳)
========================================
ذخیره و ارسال «ویدیوی آموزشی توکن» به‌صورت کاملاً اختیاری و بی‌خطا:

- منبع اول:  جدول app_settings (آپلود مستقیم از پنل ادمین — توصیه‌شده)
- منبع دوم:  متغیر TOKEN_GUIDE_VIDEO_FILE_ID در .env (fallback اولیه)
- منبع سوم:  هیچ‌کدام → متن‌ها بدون ویدیو ارسال می‌شوند و ربات به هیچ اروری نمی‌خورد ✅

فرمت ذخیره در دیتابیس (JSON):
    {"type": "video" | "animation" | "document", "file_id": "BAACAgIA..."}

فرمت env (پیشوند نوع اختیاری):
    TOKEN_GUIDE_VIDEO_FILE_ID=BAACAgIA...                      → video
    TOKEN_GUIDE_VIDEO_FILE_ID=animation:BAACAgIA...            → animation (گیف)
    TOKEN_GUIDE_VIDEO_FILE_ID=document:BAACAgIA...             → document (فایل)
"""
import json
import logging

from config import config
from database import async_session
from database.models import AppSetting

logger = logging.getLogger(__name__)

# کلید تنظیم ویدیوی آموزشی در جدول app_settings
SETTING_VIDEO_KEY = "token_guide_video"


# ============================================================
# ابزارهای عمومی key/value
# ============================================================
async def get_setting(key: str) -> str | None:
    """خواندن یک تنظیم از دیتابیس — None یعنی تنظیم نشده."""
    try:
        async with async_session() as session:
            row = await session.get(AppSetting, key)
            return row.value if row else None
    except Exception as e:
        logger.warning("get_setting(%s) failed: %s", key, e)
        return None


async def set_setting(key: str, value: str) -> bool:
    """نوشتن/به‌روزرسانی یک تنظیم (upsert)."""
    try:
        async with async_session() as session:
            row = await session.get(AppSetting, key)
            if row:
                row.value = value
            else:
                session.add(AppSetting(key=key, value=value))
            await session.commit()
        return True
    except Exception as e:
        logger.error("set_setting(%s) failed: %s", key, e)
        return False


async def delete_setting(key: str) -> bool:
    """حذف یک تنظیم (اگر وجود نداشته باشد هم خطا نمی‌دهد)."""
    try:
        async with async_session() as session:
            row = await session.get(AppSetting, key)
            if row:
                await session.delete(row)
                await session.commit()
        return True
    except Exception as e:
        logger.error("delete_setting(%s) failed: %s", key, e)
        return False


# ============================================================
# ویدیوی آموزشی توکن
# ============================================================
async def get_training_video() -> tuple[str, str] | None:
    """
    ویدیوی آموزشی فعال → (file_id, type) یا None.

    اولویت: دیتابیس (پنل ادمین) → .env → None
    هیچ‌وقت exception نمی‌دهد؛ در بدترین حالت None برمی‌گرداند.
    """
    # ۱) دیتابیس
    raw = await get_setting(SETTING_VIDEO_KEY)
    if raw:
        try:
            data = json.loads(raw)
            file_id = (data.get("file_id") or "").strip()
            mtype = data.get("type") or "video"
            if file_id and mtype in ("video", "animation", "document"):
                return file_id, mtype
        except (ValueError, AttributeError, TypeError):
            pass  # مقدار خراب → ادامه به fallback

    # ۲) .env
    env_val = (config.TOKEN_GUIDE_VIDEO_FILE_ID or "").strip()
    if env_val:
        if env_val.lower().startswith("animation:"):
            return env_val.split(":", 1)[1].strip(), "animation"
        if env_val.lower().startswith("document:"):
            return env_val.split(":", 1)[1].strip(), "document"
        return env_val, "video"

    # ۳) هیچ‌کدام
    return None


async def save_training_video(file_id: str, mtype: str = "video") -> bool:
    """ذخیره ویدیوی جدید (از پنل ادمین) — از این لحظه خودکار ارسال می‌شود."""
    if mtype not in ("video", "animation", "document"):
        mtype = "video"
    payload = json.dumps({"type": mtype, "file_id": file_id}, ensure_ascii=False)
    return await set_setting(SETTING_VIDEO_KEY, payload)


async def delete_training_video() -> bool:
    """حذف ویدیو → متن‌ها از این پس بدون ویدیو ارسال می‌شوند."""
    return await delete_setting(SETTING_VIDEO_KEY)


async def send_training_video(bot, chat_id: int, caption: str | None = None) -> bool:
    """
    ارسال ویدیوی آموزشی به یک چت — کاملاً بی‌صدا:

    - ویدیو نبود → False برمی‌گردد، هیچ اتفاقی نمی‌افتد (متن‌ها مستقل کار می‌کنند) ✅
    - خطای تلگرام (فایل حذف‌شده، بلاک و…) → فقط لاگ هشدار؛ هرگز exception نمی‌دهد ✅
    """
    try:
        media = await get_training_video()
        if not media:
            return False
        file_id, mtype = media
        if mtype == "animation":
            await bot.send_animation(chat_id, file_id, caption=caption)
        elif mtype == "document":
            await bot.send_document(chat_id, file_id, caption=caption)
        else:
            await bot.send_video(chat_id, file_id, caption=caption)
        return True
    except Exception as e:
        logger.warning("⚠️ ارسال ویدیوی آموزشی ناموفق بود (ادامه بدون ویدیو): %s", e)
        return False
