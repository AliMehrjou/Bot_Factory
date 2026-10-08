"""
🤖 Bot Factory — نقطه ورود ربات فروش
====================================
- ربات aiogram (سفارش + پنل ادمین + پنل پروکسی)
- تسک‌های پس‌زمینه: صف دیپلوی سریال، تست مداوم پروکسی، پایش اینستنس، انقضای اشتراک
اجرا:  python main.py   (یا سرویس systemd: bot-factory.service)

مستندات پایداری FSM (مربوط به تسک ۱۰):
در حال حاضر از MemoryStorage پیش‌فرض aiogram استفاده می‌شود که با ری‌استارت پاک می‌شود.
برای استفاده از RedisStorage نیاز به راه‌اندازی و اکسپوز کردن پورت Redis در سطح هاست است 
که ممکن است ریسک امنیتی داشته باشد. لذا برای حفظ سادگی و امنیت یکپارچه، 
وضعیت FSM موقت باقی مانده و تغییری در Storage ایجاد نشد.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeChat

from config import config
from database.engine import init_db, close_all
from core.deploy_queue import deploy_queue
from core import proxy_manager, health_monitor
from bot.handlers import user_router, info_router, admin_router, proxy_router, instance_proxy_router
from bot.middlewares import AdminMiddleware
from aiogram.client.session.aiohttp import AiohttpSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("factory")


_bot: Bot = None


async def notify_admins(text: str, chat_id: int = None) -> None:
    """ارسال اعلان به همه ادمین‌ها یا یک کاربر خاص — به proxy_manager و health_monitor وصل می‌شود."""
    if _bot is None:
        return
    targets = [chat_id] if chat_id else config.ADMIN_IDS
    for target in targets:
        try:
            await _bot.send_message(target, text)
        except Exception:
            pass

_bg_tasks = set()

async def on_startup(bot: Bot):
    await init_db()
    
    proxy_manager.set_notifier(notify_admins)
    health_monitor.set_notifier(notify_admins)
    from core import instance_proxies
    instance_proxies.set_notifier(notify_admins)

    await deploy_queue.start()
    
    # --- رفع مشکل H1/U1: بازیابی و شنود رویداد ری‌استارت ---
    from core.orchestrator import reconcile_redis_acls
    await reconcile_redis_acls()
    
    async def redis_restart_listener():
        while True:
            try:
                # فیلتر همه کانتینرهای redis به تعداد NUM_REDIS (پویا — نه فقط ۱ و ۲)
                args = ["docker", "events", "--filter", "event=start"]
                for i in range(1, config.NUM_REDIS + 1):
                    args += ["--filter", f"container=factory_redis_{i}"]
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE
                )
                while True:
                    line = await proc.stdout.readline()
                    if not line: break
                    await asyncio.sleep(2)  # زمان دادن به ردیس برای لود شدن
                    await reconcile_redis_acls()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Redis event listener failed: %s", e)
            
            await asyncio.sleep(10)  # اتصال مجدد در صورت قطع شدن استریم داکر
            
    t4 = asyncio.create_task(redis_restart_listener(), name="redis-events")
    # -----------------------------------------------------

    t1 = asyncio.create_task(proxy_manager.health_loop(), name="proxy-health")
    t2 = asyncio.create_task(health_monitor.instances_monitor_loop(), name="instances-monitor")
    t3 = asyncio.create_task(health_monitor.expiry_loop(), name="expiry")
    for t in [t1, t2, t3, t4]:
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
        
    from sqlalchemy import select
    from database import async_session
    from database.models import Order
    from core.deploy_queue import DeployJob
    
    async with async_session() as session:
        orders = (await session.scalars(
            select(Order).where(Order.status.in_(("approved", "provisioning")))
        )).all()
        
        # اصلاح M3: اضافه‌کردن done_cb برای اطلاع‌‌رسانی از نتیجه ساخت پس از بوت سرور
        def make_startup_cb(oid: int):
            async def cb(result: dict):
                if result.get("ok"):
                    await notify_admins(f"✅ دیپلوی مجدد bot_{oid} (پس از ری‌استارت) موفق بود.")
                else:
                    await notify_admins(f"🔴 دیپلوی مجدد bot_{oid} (پس از ری‌استارت) شکست خورد:\n{result.get('error')}")
            return cb

        for o in orders:
            if o.renew_of:
                continue
            deploy_queue.submit(DeployJob(action="provision", order_id=o.id, done_cb=make_startup_cb(o.id)))

    await bot.set_my_commands([
        BotCommand(command="start", description="سفارش ربات جدید"),
        BotCommand(command="mybots", description="ربات‌های من"),
        BotCommand(command="cancel", description="لغو عملیات جاری"),
    ])
    
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.set_my_commands([
                BotCommand(command="start", description="سفارش ربات جدید"),
                BotCommand(command="mybots", description="ربات‌های من"),
                BotCommand(command="panel", description="پنل مدیریت فکتوری"),
                BotCommand(command="proxies", description="مدیریت پروکسی‌ها"),
                BotCommand(command="cancel", description="لغو عملیات"),
            ], scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:
            logger.warning("عدم امکان ثبت منوی ادمین برای %s: %s", admin_id, e)

    msg = "🏭 <b>ربات‌ساز روشن شد</b>\nپنل: /panel | استخر پروکسی: /proxies | پروکسی هر اینستنس: /panel → مدیریت پروکسی‌ها"
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, msg)
        except Exception:
            pass
    logger.info("🚀 Bot Factory آماده است")
    

async def on_shutdown(bot: Bot):
    for t in _bg_tasks:
        t.cancel()
    await deploy_queue.stop()
    await close_all()
    logger.info("خروج امن انجام شد")

async def main():
    global _bot
    
    # === اتصال مستقیم به پروکسی V2rayN / فیلترشکن ===
    session = AiohttpSession(proxy=config.OUTBOUND_PROXY or None)
    
    _bot = Bot(
        token=config.SALES_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session  # سشن حاوی پروکسی در اینجا به ربات داده می‌شود
    )
    dp = Dispatcher()

    # ثبت میدل‌ور بررسی دسترسی ادمین روی روتِرهای مدیریتی
    admin_router.message.middleware(AdminMiddleware())
    admin_router.callback_query.middleware(AdminMiddleware())
    proxy_router.message.middleware(AdminMiddleware())
    proxy_router.callback_query.middleware(AdminMiddleware())
    instance_proxy_router.message.middleware(AdminMiddleware())
    instance_proxy_router.callback_query.middleware(AdminMiddleware())

    dp.include_routers(user_router, info_router, admin_router, proxy_router, instance_proxy_router)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    drop_updates = getattr(config, 'DROP_PENDING_UPDATES', False)
    await _bot.delete_webhook(drop_pending_updates=drop_updates)
    await dp.start_polling(_bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("خروج با Ctrl+C")