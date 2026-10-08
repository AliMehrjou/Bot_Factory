"""
میدل‌ورهای ربات
=============
حاوی میدل‌ور یکپارچه برای بررسی دسترسی ادمین روی پنل‌های مدیریتی.
"""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery
from config import config

class AdminMiddleware(BaseMiddleware):
    """میدل‌ور متمرکز بررسی دسترسی ادمین برای پنل‌های مدیریتی."""
    
    async def __call__(self, handler, event, data):
        # اگر کاربر در لیست ادمین‌ها نباشد، دسترسی مسدود می‌شود
        if event.from_user.id not in config.ADMIN_IDS:
            if isinstance(event, CallbackQuery):
                await event.answer("⛔️ فقط ادمین", show_alert=True)
            # پیام‌های معمولی نادیده گرفته می‌شوند (سایلنت)
            return
        
        # در صورت داشتن دسترسی، هندلر مربوطه اجرا می‌شود
        return await handler(event, data)