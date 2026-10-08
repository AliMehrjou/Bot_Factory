"""
🌐 Proxy Manager — پل مرکزی پروکسی ربات‌ساز
============================================
درخواست کارفرما:
  «بخشی برای افزودن لیست پروکسی + رفتن خودکار پروکسی‌ها به قسمت لاگین
   + تست مداوم و هندلینگ تا کیفیت سیستم بالای بماند»

پیاده‌سازی (بر اساس سورس واقعی sender_bot):
  ۱) افزودن لیست پروکسی از پنل ادمین (چند خطی، فرمت‌های مختلف، ضدتکرار)
  ۲) حلقه تست مداوم (هر PROXY_CHECK_INTERVAL ثانیه):
     اتصال TCP از طریق پروکسی به DC تلگرام (149.154.167.50:443) + اندازه‌گیری پینگ
     ماشین وضعیت: active / weak / dead / disabled
       • ۳ شکست پیاپی  → dead   (فوراً از سینک خارج می‌شود)
       • ۲ موفقیت پیاپی → بازیابی
       • پینگ > 1200ms  → weak
  ۳) سینک خودکار به جدول `proxies` دیتابیس هر اینستنس (usage_type='login')
     — دقیقاً همان جدولی که login_handlers.py هنگام لاگین از آن پراکسی تصادفی سالم برمی‌دارد.
     سطرهای مرده → is_active=0 / health_state='DEAD' تا لاگین روی آن‌ها ننشیند.
  ۴) اطلاع‌رسانی به ادمین در تغییر وضعیت‌ها (batch، بدون اسپم).
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from python_socks.async_.asyncio import Proxy as AsyncProxy
from sqlalchemy import select, delete, update, func

from config import config
from database import async_session
from database.engine import get_shared_app_engine, drop_instance_engine
from database.models import FactoryProxy, Order, SyncLog
from utils.proxy_parse import ParsedProxy, parse_proxies_text, mask_proxy

logger = logging.getLogger(__name__)

_check_lock = asyncio.Lock()
_sync_lock = asyncio.Lock()
_notifier = None            # async callable(text) — از main.py وصل می‌شود
_last_cycle_at = None
_cycle_count = 0
_sync_summary = {"ok": 0, "fail": 0, "last": None}


# ------------------------------------------------------------
# اتصال به ادمین
# ------------------------------------------------------------
def set_notifier(fn) -> None:
    global _notifier
    _notifier = fn


async def _notify(text: str) -> None:
    if _notifier is None:
        return
    try:
        await _notifier(text)
    except Exception:
        logger.exception("ارسال اعلان پروکسی ناموفق")


# ------------------------------------------------------------
# افزودن پروکسی
# ------------------------------------------------------------
async def add_proxies_from_text(text: str) -> dict:
    """افزودن چند خط پروکسی. خروجی: آمار برای نمایش به ادمین."""
    parsed, invalid = parse_proxies_text(text)
    if not parsed and not invalid:
        return {"added": 0, "duplicate": 0, "invalid": 0, "msg": "هیچ خط قابل شناسایی نبود."}

    async with async_session() as session:
        existing = set(
            (await session.scalars(select(FactoryProxy.proxy_string))).all()
        )
        added = 0
        for p in parsed:
            if p.proxy_string in existing:
                continue
            session.add(FactoryProxy(
                scheme=p.scheme, host=p.host, port=p.port,
                username=p.username, password=p.password,
                proxy_string=p.proxy_string, status="active",
            ))
            existing.add(p.proxy_string)
            added += 1
        await session.commit()

    return {
        "added": added,
        "duplicate": len(parsed) - added,
        "invalid": len(invalid),
        "invalid_lines": invalid[:5],
        "msg": f"✅ {added} پروکسی جدید ثبت شد"
               f" | تکراری: {len(parsed) - added}"
               f" | نامعتبر: {len(invalid)}",
    }


_bg_tasks = set()

async def remove_proxy(proxy_id: int) -> bool:
    async with async_session() as session:
        p = await session.get(FactoryProxy, int(proxy_id))
        if not p:
            return False
        await session.delete(p)
        await session.commit()
    t = asyncio.create_task(sync_all_instances("حذف دستی پروکسی"))
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return True


async def purge_dead() -> int:
    """حذف کامل پروکسی‌های مرده از فکتوری (از اینستنس‌ها فقط غیرفعال شده بودند)."""
    async with async_session() as session:
        res = await session.execute(delete(FactoryProxy).where(FactoryProxy.status == "dead"))
        await session.commit()
        return res.rowcount or 0


async def disable_proxy(proxy_id: int) -> bool:
    async with async_session() as session:
        await session.execute(
            update(FactoryProxy).where(FactoryProxy.id == int(proxy_id))
            .values(status="disabled"))
        await session.commit()
    
    t = asyncio.create_task(sync_all_instances("غیرفعال‌سازی دستی پروکسی"))
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return True


# ------------------------------------------------------------
# تست تک‌پروکسی
# ------------------------------------------------------------
async def _check_one(p: FactoryProxy) -> tuple:
    """اتصال از طریق پروکسی به DC تلگرام؛ خروجی (ok, latency_ms|None)."""
    url = f"{p.scheme}://{p.username}:{p.password}@{p.host}:{p.port}" if p.username \
        else f"{p.scheme}://{p.host}:{p.port}"
    start = time.monotonic()
    try:
        proxy = AsyncProxy.from_url(url)
        sock = await asyncio.wait_for(
            proxy.connect(dest_host=config.PROXY_TEST_HOST, dest_port=config.PROXY_TEST_PORT),
            timeout=config.PROXY_CHECK_TIMEOUT,
        )
        latency = int((time.monotonic() - start) * 1000)
        try:
            sock.close()
        except Exception:
            pass
        return True, latency
    except Exception:
        return False, None


# ------------------------------------------------------------
# حلقه تست مداوم + سینک
# ------------------------------------------------------------
# بازنویسی کامل تابع `check_cycle` در فایل `core/proxy_manager.py` (محدوده جایگزینی: خطوط ۱۷۰ تا ۲۳۹)

async def check_cycle(force: bool = False) -> dict:
    """
    یک دور کامل تست همه پروکسی‌ها + سینک در صورت تغییر یا افزودن پروکسی جدید.
    (هم برای حلقه پس‌زمینه هم برای دکمه «تست همه» ادمین استفاده می‌شود.)
    """
    if _check_lock.locked() and not force:
        return {"skipped": True}
    async with _check_lock:
        global _last_cycle_at, _cycle_count
        _last_cycle_at = datetime.now(timezone.utc)
        _cycle_count += 1

        # تراکنش اول: فقط خواندن داده‌ها به حافظه و بستن تراکنش
        async with async_session() as session:
            proxies_db = (await session.scalars(
                select(FactoryProxy).where(FactoryProxy.status != "disabled")
            )).all()
            proxies_data = [
                {
                    "id": p.id, "proxy_string": p.proxy_string, "status": p.status,
                    "consec_ok": p.consec_ok, "consec_fail": p.consec_fail,
                    "scheme": p.scheme, "host": p.host, "port": p.port,
                    "username": p.username, "password": p.password,
                    "last_synced_at": p.last_synced_at
                }
                for p in proxies_db
            ]

        sem = asyncio.Semaphore(max(1, config.PROXY_CONCURRENCY))
        async def guarded(pd):
            async with sem:
                # ساخت آبجکت موقت برای تابع تست (خارج از سشن دیتابیس)
                temp_p = FactoryProxy(
                    scheme=pd["scheme"], host=pd["host"], port=pd["port"],
                    username=pd["username"], password=pd["password"]
                )
                return await _check_one(temp_p)

        # اجرای تست‌های شبکه بدون بلاک کردن کانکشن دیتابیس
        results = await asyncio.gather(*[guarded(pd) for pd in proxies_data], return_exceptions=True)

        transitions = []
        needs_sync = False

        # تراکنش دوم: فقط نوشتن نتایج به‌روزرسانی‌شده
        async with async_session() as session:
            for pd, res in zip(proxies_data, results):
                old = pd["status"]
                if isinstance(res, Exception):
                    ok, latency = False, None
                else:
                    ok, latency = res

                new_consec_ok = pd["consec_ok"]
                new_consec_fail = pd["consec_fail"]

                if ok:
                    new = "weak" if (latency or 0) > config.PROXY_WEAK_MS else "active"
                    if old == "dead" and (new_consec_ok + 1) < config.PROXY_RECOVER_OKS:
                        new = "dead"
                    new_consec_ok += 1
                    new_consec_fail = 0
                else:
                    new_consec_ok = 0
                    new_consec_fail += 1
                    latency = None
                    new = "dead" if new_consec_fail >= config.PROXY_DEAD_FAILS else old

                if new != old:
                    transitions.append((mask_proxy(pd["proxy_string"]), old, new))
                
                # شناسایی پروکسی سالم که به‌تازگی اضافه شده و هنوز سینک نشده است (M1)
                if new in ("active", "weak") and pd["last_synced_at"] is None:
                    needs_sync = True

                await session.execute(
                    update(FactoryProxy).where(FactoryProxy.id == pd["id"])
                    .values(
                        status=new, consec_ok=new_consec_ok, consec_fail=new_consec_fail,
                        ping_ms=latency, last_checked_at=datetime.now(timezone.utc)
                    )
                )
            await session.commit()

        # سینک در صورت تغییر وضعیت، وجود پروکسی جدید، یا سیکل‌های ۵
        synced = 0
        if transitions or needs_sync or _cycle_count % 5 == 0:
            synced = await sync_all_instances()

        # اعلان ادمین
        if transitions:
            lines = ["🌐 <b>تغییر وضعیت پروکسی‌ها:</b>"]
            for ps, old, new in transitions[:15]:
                icon = {"active": "🟢", "weak": "🟡", "dead": "🔴"}.get(new, "⚪")
                lines.append(f"{icon} <code>{ps}</code> : {old} → <b>{new}</b>")
            if len(transitions) > 15:
                lines.append(f"… و {len(transitions) - 15} مورد دیگر")
            if synced:
                lines.append(f"\n🔄 سینک به {synced} اینستنس انجام شد.")
            await _notify("\n".join(lines))
        elif _cycle_count % config.PROXY_NOTIFY_HEARTBEAT == 0:
            st = await stats()
            await _notify(
                f"🌐 <b>ضربان پروکسی‌ها</b> (سیکل {_cycle_count})\n"
                f"🟢 سالم: {st['active']} | 🟡 کند: {st['weak']} | 🔴 مُرد: {st['dead']}\n"
                f"⏱ میانگین پینگ: {st['avg_ping'] or '—'} ms"
            )

        return {"checked": len(proxies_data), "transitions": transitions, "synced": synced}

async def health_loop() -> None:
    """تسک پس‌زمینه — تست مداوم تا سیستم با کیفیت بماند."""
    logger.info("🌐 Proxy Health Loop روشن شد (هر %s ثانیه)", config.PROXY_CHECK_INTERVAL)
    while True:
        try:
            await check_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("خطا در حلقه تست پروکسی")
        await asyncio.sleep(config.PROXY_CHECK_INTERVAL)



def _desired_for_instance(order_id: int, healthy: list) -> dict:
    """زیرمجموعه‌ی قطعی و چرخشی از پروکسی‌های سالم برای یک اینستنس (رفع اشتراک کامل استخر)."""
    n = config.PROXIES_PER_INSTANCE
    if n <= 0 or len(healthy) <= n:
        return {
            p.proxy_string: {
                "health_state": "HEALTHY" if p.status == "active" else "WEAK",
                "is_healthy": 1 if p.status == "active" else 0,
                "ping_ms": p.ping_ms,
            }
            for p in healthy
        }
    
    start = (int(order_id) * n) % len(healthy)
    picked = [healthy[(start + i) % len(healthy)] for i in range(n)]
    return {
        p.proxy_string: {
            "health_state": "HEALTHY" if p.status == "active" else "WEAK",
            "is_healthy": 1 if p.status == "active" else 0,
            "ping_ms": p.ping_ms,
        }
        for p in picked
    }

# ------------------------------------------------------------
# سینک به اینستنس‌ها (قسمت لاگین)
# ------------------------------------------------------------
async def sync_all_instances(reason: str = "") -> int:
    """
    نوشتن پروکسی‌های سالم فکتوری در جدول proxies هر اینستنس (usage_type=login).
    • درج/به‌روزرسانی: is_active=1, health_state=HEALTHY|WEAK, ping_ms
    • سطرهای ناموجود در فکتوری (مرده/حذف‌شده): is_active=0, health_state=DEAD
    ⚠️ never DELETE — شمارنده in_use و fail_count اینستنس دست‌نخورده می‌ماند.
    """
    if _sync_lock.locked():
        logger.warning("سینک در حال اجراست، درخواست جدید نادیده گرفته شد.")
        return 0
    async with _sync_lock:
        from sqlalchemy import text
        async with async_session() as session:
            rows = (await session.scalars(
                select(FactoryProxy)
                .where(FactoryProxy.status.in_(("active", "weak")))
                .order_by(FactoryProxy.id)
            )).all()
            
            if not rows:
                logger.warning("استخر پروکسی‌های سالم فکتوری خالی است. سینک متوقف شد.")
                await _notify("⚠️ <b>هشدار فکتوری:</b> لیست پروکسی‌های سالم کاملاً خالی است. برای جلوگیری از قطعی لاگین اینستنس‌ها، عملیات سینک لغو شد.")
                return 0

            orders = (await session.scalars(
                select(Order).where(Order.status.in_(("deployed", "stopped")))
            )).all()
            order_ids = [o.id for o in orders]

        ok_count = 0
        total_synced_proxies = 0
        for oid in order_ids:
            ok = True
            desired = _desired_for_instance(oid, rows)
            msg = f"{len(desired)} پروکسی سینک شد. {reason}"
            try:
                await _sync_one_instance(int(oid), desired)
                ok_count += 1
                total_synced_proxies = max(total_synced_proxies, len(desired))
                
                if desired:
                    async with async_session() as update_session:
                        now = datetime.now(timezone.utc)
                        await update_session.execute(
                            update(FactoryProxy)
                            .where(FactoryProxy.proxy_string.in_(list(desired.keys())))
                            .values(last_synced_at=now)
                        )
                        await update_session.commit()
            except Exception as e:
                ok = False
                msg = f"خطا در سینک: {e}"
                logger.warning("سینک به sender_%s ناموفق: %s", oid, e)
            try:
                async with async_session() as session:
                    session.add(SyncLog(order_id=int(oid), ok=ok, detail=msg))
                    await session.execute(text(
                        "DELETE FROM sync_logs WHERE order_id = :oid AND id NOT IN ("
                        "  SELECT id FROM ("
                        "    SELECT id FROM sync_logs WHERE order_id = :oid ORDER BY id DESC LIMIT 20"
                        "  ) AS tmp"
                        ")"
                    ), {"oid": int(oid)})
                    await session.commit()
            except Exception:
                pass

        global _sync_summary
        _sync_summary = {
            "ok": ok_count,
            "fail": len(order_ids) - ok_count,
            "last": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "proxies": total_synced_proxies,
        }
        return ok_count


async def sync_instance(order_id: int, timeout: int = 120) -> None:
    """تا N ثانیه منتظر ساخته شدن جدول proxies می‌ماند و سینک می‌کند."""
    deadline = time.monotonic() + timeout
    db_name = config.db_name_for(order_id)
    from database.engine import get_shared_app_engine
    eng = get_shared_app_engine()
    from sqlalchemy import text
    
    from core.orchestrator import status

    while time.monotonic() < deadline:
        container_status = await status(order_id)
        if container_status != "running":
            logger.warning(f"کانتینر bot_{order_id} در وضعیت running نیست (وضعیت: {container_status}). انتظار برای سینک لغو شد.")
            return

        try:
            async with eng.connect() as conn:
                res = await conn.execute(text(f"SHOW TABLES FROM `{db_name}` LIKE 'proxies'"))
                if res.scalar():
                    break
        except Exception:
            pass
        await asyncio.sleep(5)
    
    if time.monotonic() >= deadline:
        logger.warning(f"جدول proxies برای {order_id} ساخته نشد!")
        return

    async with async_session() as session:
        rows = (await session.scalars(
            select(FactoryProxy)
            .where(FactoryProxy.status.in_(("active", "weak")))
            .order_by(FactoryProxy.id)
        )).all()
        
    desired = _desired_for_instance(order_id, rows)
    
    try:
        await _sync_one_instance(order_id, desired)
        if desired:
            async with async_session() as update_session:
                now = datetime.now(timezone.utc)
                await update_session.execute(
                    update(FactoryProxy)
                    .where(FactoryProxy.proxy_string.in_(list(desired.keys())))
                    .values(last_synced_at=now)
                )
                await update_session.commit()
    except Exception as e:
        logger.error(f"Error syncing {order_id}: {e}")


async def _sync_one_instance(order_id: int, desired: dict) -> None:
    db_name = config.db_name_for(order_id)
    from database.engine import get_shared_app_engine
    eng = get_shared_app_engine()

    from sqlalchemy import text, select
    from database.models import FactoryProxy

    # دریافت تمامی پروکسی‌های شناخته‌شده‌ی فکتوری برای جلوگیری از کشتن پروکسی‌های دستی مشتری
    async with async_session() as session:
        known_to_factory = set((await session.scalars(select(FactoryProxy.proxy_string))).all())

    async with eng.begin() as conn:
        # ۱) وضعیت فعلی اینستنس
        current = dict(
            (await conn.execute(text(
                f"SELECT proxy_string, is_active FROM `{db_name}`.`proxies` WHERE usage_type IN ('login','both')"
            ))).all()
        )

        # ۲) درج/به‌‌روزرسانی پروکسی‌های سالم (با حفظ usage_type='both' تنظیم‌شده توسط مشتری)
        if desired:
            await conn.execute(
                text(
                    f"INSERT INTO `{db_name}`.`proxies` "
                    "(proxy_string, is_active, usage_type, is_healthy, health_state, ping_ms, last_checked_at) "
                    "VALUES (:ps, 1, :ut, :ih, :hs, :pm, NOW()) "
                    "ON DUPLICATE KEY UPDATE "
                    "  is_active=1, usage_type=IF(usage_type='both', 'both', VALUES(usage_type)), is_healthy=VALUES(is_healthy), "
                    "  health_state=VALUES(health_state), ping_ms=VALUES(ping_ms), "
                    "  last_checked_at=NOW()"
                ),
                [
                    {
                        "ps": ps, "ut": config.PROXY_USAGE_TYPE,
                        "ih": v["is_healthy"], "hs": v["health_state"], "pm": v["ping_ms"],
                    }
                    for ps, v in desired.items()
                ],
            )

        # ۳) غیرفعال‌سازی سطرهایی که در استخر سالم فکتوری نیستند
        # شرط ps in known_to_factory تضمین می‌کند پروکسی شخصی کاربر را دستکاری نکنیم
        to_disable = [
            ps for ps, act in current.items() 
            if act and ps in known_to_factory and ps not in desired
        ]
        for ps in to_disable:
            await conn.execute(
                text(
                    f"UPDATE `{db_name}`.`proxies` SET is_active=0, is_healthy=0, health_state='DEAD' "
                    "WHERE proxy_string = :ps"
                ),
                {"ps": ps},
            )

    logger.info("🔄 سینک %s پروکسی به %s (%s غیرفعال شد)", len(desired), db_name, len(to_disable))
 

async def drop_engine_for(order_id: int) -> None:
    await drop_instance_engine(config.db_name_for(order_id))


# ------------------------------------------------------------
# بهترین پروکسی (برای LOGIN_PROXY_URL اولیه اینستنس)
# ------------------------------------------------------------
async def get_best_proxy_url() -> str:
    async with async_session() as session:
        p = (await session.scalars(
            select(FactoryProxy)
            .where(FactoryProxy.status == "active")
            .order_by(FactoryProxy.ping_ms.is_(None), FactoryProxy.ping_ms.asc())
            .limit(1)
        )).first()
        if not p:
            return ""
        return f"{p.scheme}://{p.username}:{p.password}@{p.host}:{p.port}" if p.username \
            else f"{p.scheme}://{p.host}:{p.port}"


# ------------------------------------------------------------
# گزارش و خروجی
# ------------------------------------------------------------
# بازنویسی کامل تابع `stats` در فایل `core/proxy_manager.py` (محدوده جایگزینی: خطوط ۴۶۵ تا ۴۸۷)

async def stats() -> dict:
    async with async_session() as session:
        rows = (await session.execute(
            select(FactoryProxy.status, func.count(FactoryProxy.id))
            .group_by(FactoryProxy.status)
        )).all()
        by = {r[0]: int(r[1]) for r in rows}
        avg = await session.scalar(select(func.avg(FactoryProxy.ping_ms))
                                   .where(FactoryProxy.status == "active"))
        deployed = await session.scalar(
            select(func.count(Order.id)).where(Order.status.in_(("deployed", "stopped"))))
        
        from sqlalchemy import text
        sync_logs = (await session.execute(text(
            "SELECT order_id, ok, detail, created_at "
            "FROM sync_logs s1 "
            "WHERE id = (SELECT MAX(id) FROM sync_logs s2 WHERE s1.order_id = s2.order_id) "
            "ORDER BY created_at DESC"
        ))).all()

    sync_ok = sum(1 for r in sync_logs if r.ok)
    sync_fail = sum(1 for r in sync_logs if not r.ok)
    last_sync_time = sync_logs[0].created_at.strftime("%H:%M:%S") if sync_logs else "—"
    
    instance_details = []
    for r in sync_logs[:5]: # نمایش ۵ رکورد آخر
        icon = "✅" if r.ok else "❌"
        instance_details.append(f"{icon} اینستنس {r.order_id}: {r.detail}")

    st = {
        "active": by.get("active", 0),
        "weak": by.get("weak", 0),
        "dead": by.get("dead", 0),
        "disabled": by.get("disabled", 0),
        "total": sum(by.values()),
        "avg_ping": int(avg) if avg else None,
        "instances": int(deployed or 0),
        "last_cycle": _last_cycle_at.strftime("%H:%M:%S") if _last_cycle_at else "—",
        "cycle": _cycle_count,
        "sync": {
            "ok": sync_ok,
            "fail": sync_fail,
            "last": last_sync_time,
            "proxies": by.get("active", 0) + by.get("weak", 0),
            "details": "\n".join(instance_details) or "هیچ رکوردی یافت نشد."
        }
    }
    return st

async def export_text() -> str:
    """خروجی متنی همه پروکسی‌ها برای بکاپ/انتقال."""
    async with async_session() as session:
        rows = (await session.scalars(
            select(FactoryProxy).order_by(FactoryProxy.status, FactoryProxy.id)
        )).all()
    if not rows:
        return "(لیست خالی است)"
    lines = []
    for p in rows:
        url = f"{p.scheme}://{p.username}:{p.password}@{p.host}:{p.port}" if p.username \
            else f"{p.scheme}://{p.host}:{p.port}"
        lines.append(f"{url}  # {p.status} {p.ping_ms or '-'}ms")
    return "\n".join(lines)


async def top_list(limit: int = 20) -> str:
    async with async_session() as session:
        rows = (await session.scalars(
            select(FactoryProxy)
            .where(FactoryProxy.status.in_(("active", "weak")))
            .order_by(FactoryProxy.ping_ms.is_(None), FactoryProxy.ping_ms.asc())
            .limit(limit)
        )).all()
    if not rows:
        return "(پروکسی سالمی موجود نیست — از «افزودن» استفاده کنید)"
    
    async with async_session() as session:
        total_healthy = await session.scalar(select(func.count(FactoryProxy.id)).where(FactoryProxy.status.in_(("active", "weak")))) or 0

    icons = {"active": "🟢", "weak": "🟡", "dead": "🔴", "disabled": "⚪"}
    lines = [f"<b>🌐 بهترین پروکسی‌ها:</b> (کل سالم: {total_healthy} | هر اینستنس: {config.PROXIES_PER_INSTANCE} پروکسی)", ""]
    for p in rows:
        lines.append(
            f"{icons.get(p.status, '⚪')} <code>{mask_proxy(p.proxy_string)}</code>"
            f"  ⏱ {p.ping_ms if p.ping_ms is not None else '—'}ms"
            f"  (#{p.id})"
        )
    return "\n".join(lines)

async def top_list_with_objects(limit: int = 20) -> tuple[str, list]:
    async with async_session() as session:
        rows = (await session.scalars(
            select(FactoryProxy)
            .where(FactoryProxy.status.in_(("active", "weak", "disabled", "dead")))
            .order_by(FactoryProxy.ping_ms.is_(None), FactoryProxy.ping_ms.asc())
            .limit(limit)
        )).all()
    if not rows:
        return "(پروکسی موجود نیست — از «افزودن» استفاده کنید)", []
    
    async with async_session() as session:
        total_healthy = await session.scalar(select(func.count(FactoryProxy.id)).where(FactoryProxy.status.in_(("active", "weak")))) or 0

    icons = {"active": "🟢", "weak": "🟡", "dead": "🔴", "disabled": "⚪"}
    lines = [f"<b>🌐 لیست پروکسی‌ها:</b> (کل سالم: {total_healthy} | هر اینستنس: {config.PROXIES_PER_INSTANCE} پروکسی)", ""]
    for p in rows:
        lines.append(
            f"{icons.get(p.status, '⚪')} <code>{mask_proxy(p.proxy_string)}</code>"
            f"  ⏱ {p.ping_ms if p.ping_ms is not None else '—'}ms"
            f"  (#{p.id})"
        )
    return "\n".join(lines), rows