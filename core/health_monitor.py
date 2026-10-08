"""
پایش سلامت اینستنس‌ها + انقضای اشتراک
=====================================
- هر ۵ دقیقه: وضعیت کانتینر همه سفارش‌های deployed چک می‌شود؛
  در صورت خاموشی، تلاش برای روشن کردن + اعلان throttle شده به ادمین.
- هر ساعت: سفارش‌های منقضی علامت می‌خورند (و در صورت فعال بودن EXPIRY_AUTO_STOP، متوقف).
"""
import asyncio
import logging
import datetime as dt
from datetime import datetime, timezone

from sqlalchemy import select, text

from config import config
from core import orchestrator
from database import async_session
from database.models import Order

logger = logging.getLogger(__name__)

_notify = None
_last_alert: dict = {}          # order_id -> ts (throttle 1 ساعت)
INTERVAL = 300


def set_notifier(fn) -> None:
    global _notify
    _notify = fn


async def _alert(msg: str, key: str) -> None:
    now = datetime.now(timezone.utc).timestamp()
    if len(_last_alert) > 500:
        expired = [k for k, v in _last_alert.items() if now - v > 3600]
        for k in expired:
            _last_alert.pop(k, None)

    if _last_alert.get(key, 0) + 3600 > now:
        return
    _last_alert[key] = now
    if _notify:
        try:
            await _notify(msg)
        except Exception:
            pass

async def instances_monitor_loop() -> None:
    logger.info("🩺 Instance Monitor روشن شد (هر %ss)", INTERVAL)
    while True:
        try:
            async with async_session() as session:
                orders = (await session.scalars(
                    select(Order).where(Order.status == "deployed")
                )).all()
                deployed_orders = {str(o.id): o for o in orders}

            if deployed_orders:
                # فراخوانی فقط یک دستور برای تمام اینستنس‌ها
                res = await orchestrator.run_cmd(
                    ["docker", "ps", "-a", "--filter", "name=^bot_", "--format", "{{.Names}}|{{.State}}"],
                    timeout=30, check=False
                )
                
                states = {}
                if res.returncode == 0:
                    for line in (res.stdout or "").strip().split("\n"):
                        if line:
                            name, state = line.split("|", 1)
                            if name.startswith("bot_"):
                                states[name[4:]] = state.lower()

                for oid, o in deployed_orders.items():
                    state = states.get(oid, "missing")
                    running = (state == "running")
                    
                    if not running:
                        # H5: بازبینی وضعیت در دیتابیس قبل از استارت خودکار برای جلوگیری از race
                        async with async_session() as session:
                            current_status = await session.scalar(select(Order.status).where(Order.id == int(oid)))
                        if current_status != "deployed":
                            continue

                        await _alert(
                            f"⚠️ اینستنس <b>bot_{oid}</b> خاموش است ({state}). "
                            f"تلاش برای اجرای مجدد...",
                            key=f"down:{oid}",
                        )
                        try:
                            await orchestrator.start(int(oid))
                        except Exception as e:
                            await _alert(
                                f"🔴 اجرای مجدد bot_{oid} ناموفق: {e}",
                                key=f"restart:{oid}",
                            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("خطای حلقه پایش اینستنس")
        await asyncio.sleep(INTERVAL)
    
async def expiry_loop() -> None:
    """چک ساعتی انقضای اشتراک مشتری‌ها و هشدارهای پیش از انقضا."""
    logger.info("⏰ Expiry Loop روشن شد")
    while True:
        try:
            async with async_session() as session:
                active_orders = (await session.scalars(
                    select(Order).where(Order.status == "deployed", Order.expires_at.isnot(None))
                )).all()
                now = datetime.now(timezone.utc)

                for o in active_orders:
                    if o.expires_at.tzinfo is None:
                        o.expires_at = o.expires_at.replace(tzinfo=timezone.utc)
                    
                    bot_disp = f"@{o.bot_username}" if o.bot_username else f"(bot_{o.id})"
                    
                    if o.expires_at <= now:
                        await session.execute(
                            text("UPDATE orders SET status='expired' WHERE id=:o"), {"o": o.id})
                        msg = f"⌛️ اشتراک ربات {bot_disp} منقضی شد."
                        if config.EXPIRY_AUTO_STOP:
                            msg += " (خاموش شد)"
                        await _alert(msg, key=f"exp:{o.id}")
                        if _notify:
                            try:
                                await _notify(f"⚠️ <b>اشتراک ربات شما ({bot_disp}) منقضی شد!</b>\nبرای تمدید به /mybots مراجعه کنید.", chat_id=o.tg_id)
                            except Exception:
                                pass
                        
                        if config.EXPIRY_AUTO_STOP:
                            try:
                                await orchestrator.stop(int(o.id))
                            except Exception:
                                logger.exception("stop اینستنس منقضی ناموفق: bot_%s", o.id)
                    
                    elif getattr(config, 'EXPIRY_WARN_DAYS', 0) > 0 and o.warned_at is None:
                        warn_time = o.expires_at - dt.timedelta(days=config.EXPIRY_WARN_DAYS)
                        if warn_time <= now:
                            await session.execute(
                                text("UPDATE orders SET warned_at=:now WHERE id=:o"), {"now": now, "o": o.id})
                            await _alert(f"⚠ اشتراک ربات {bot_disp} رو به اتمام است.", key=f"warn:{o.id}")
                            if _notify:
                                try:
                                    await _notify(f"⏳ <b>هشدار:</b> اشتراک ربات شما ({bot_disp}) کمتر از {config.EXPIRY_WARN_DAYS} روز دیگر منقضی می‌شود.\nجهت جلوگیری از قطعی، لطفاً از /mybots تمدید کنید.", chat_id=o.tg_id)
                                except Exception:
                                    pass

                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("خطای حلقه انقضا")
        await asyncio.sleep(3600)