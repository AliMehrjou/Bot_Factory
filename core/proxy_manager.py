"""
🌐 Proxy Manager — پل مرکزی پروکسی ربات‌ساز (نسخه ۲: مدیریت تفکیک‌شده هر اینستنس)
=====================================================================================
درخواست کارفرما:
  «خودم می‌خواهم از طریق ربات‌ساز برای هر اینستنس پروکسی لاگین و سندر اضافه کنم،
   حذف/ویرایش/تست بگیرم و کاربر اصلاً دخلی نداشته باشد.»

پیاده‌سازی (بر اساس سورس واقعی sender_bot):
  ۱) استخر مرکزی factory_proxies = انبار واحد پروکسی + تست مداوم (هر PROXY_CHECK_INTERVAL):
     اتصال TCP از طریق پروکسی به DC تلگرام (149.154.167.50:443) + اندازه‌گیری پینگ
     ماشین وضعیت: active / weak / dead / disabled
       • ۳ شکست پیاپی  → dead   (فوراً از سینک خارج می‌شود)
       • ۲ موفقیت پیاپی → بازیابی
       • پینگ > 1200ms  → weak
  ۲) 🎯 لایه تخصیص instance_proxies (جدید):
     هر اینستنس فقط پروکسی‌های «تخصیص‌یافته و روشن» خودش را — با usage_type مخصوص
     خودش (login/sender/both) — در جدول proxies دیتابیس خودش می‌گیرد.
     اینستنس بدون تخصیص → حالت legacy چرخش عمومی استخر سالم (سازگاری نسخه قبل).
  ۳) سطرهای خارج از تخصیص/مرده → is_active=0 / health_state='DEAD'
     (هرگز DELETE نمی‌کنیم — شمارنده in_use و fail_count اینستنس دست‌نخورده می‌ماند)
  ۴) اطلاع‌رسانی به ادمین در تغییر وضعیت‌ها (batch + نام اینستنس‌های متأثر).
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
from database.models import FactoryProxy, InstanceProxy, Order, SyncLog
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


async def _deactivate_in_all_instances(proxy_strings: list) -> int:
    """
    🛠 رفع باگ مهم نسخه قبل: وقتی پروکسی از استخر مرکزی حذف می‌شد، ردیف فعالش
    در دیتابیس اینستنس‌ها باقی می‌ماند (چون دیگر member «known_to_factory» نبود و
    سینک آن را deactive نمی‌کرد). حالا قبل از حذف، رشته در «همه» دیتابیس‌های
    اینستنس‌های فعال/متوقف غیرفعال می‌شود تا لاگین/سندر روی آن ننشیند.
    """
    if not proxy_strings:
        return 0
    from sqlalchemy import text as _text, bindparam
    from database.engine import get_shared_app_engine
    eng = get_shared_app_engine()

    async with async_session() as session:
        orders = (await session.scalars(
            select(Order.id).where(Order.status.in_(("deployed", "stopped")))
        )).all()

    done = 0
    for oid in orders:
        db_name = config.db_name_for(int(oid))
        try:
            async with eng.begin() as conn:
                await conn.execute(
                    _text(
                        f"UPDATE `{db_name}`.`proxies` "
                        "SET is_active=0, is_healthy=0, health_state='DEAD' "
                        "WHERE proxy_string IN :ps"
                    ).bindparams(bindparam("ps", expanding=True)),
                    {"ps": list(proxy_strings)},
                )
            done += 1
        except Exception as e:
            logger.debug("غیرفعال‌سازی %s در %s ناموفق: %s", proxy_strings[:2], db_name, e)
    return done


async def remove_proxy(proxy_id: int) -> bool:
    """
    حذف کامل پروکسی از استخر مرکزی + پاکسازی تخصیص‌ها + غیرفعال‌سازی در
    دیتابیس همه اینستنس‌ها (بدون حذف ردیف — شمارنده‌های اینستنس حفظ می‌شود).
    """
    async with async_session() as session:
        p = await session.get(FactoryProxy, int(proxy_id))
        if not p:
            return False
        proxy_string = p.proxy_string
        # ۱) حذف تخصیص‌های این پروکسی از همه اینستنس‌ها
        await session.execute(
            delete(InstanceProxy).where(InstanceProxy.proxy_id == int(proxy_id)))
        await session.delete(p)
        await session.commit()

    # ۲) غیرفعال‌سازی در دیتابیس همه اینستنس‌های زنده
    n = await _deactivate_in_all_instances([proxy_string])
    logger.info("🗑 پروکسی %s حذف شد (در %s دیتابیس اینستنس غیرفعال گردید)",
                mask_proxy(proxy_string), n)
    return True


async def purge_dead() -> int:
    """حذف کامل پروکسی‌های مرده از فکتوری + غیرفعال‌سازی ردیف‌هایشان در اینستنس‌ها."""
    async with async_session() as session:
        dead_strings = set((await session.scalars(
            select(FactoryProxy.proxy_string).where(FactoryProxy.status == "dead")
        )).all())
        dead_ids = set((await session.scalars(
            select(FactoryProxy.id).where(FactoryProxy.status == "dead")
        )).all())
        if not dead_ids:
            return 0
        await session.execute(
            delete(InstanceProxy).where(InstanceProxy.proxy_id.in_(dead_ids)))
        res = await session.execute(
            delete(FactoryProxy).where(FactoryProxy.status == "dead"))
        await session.commit()
        n = res.rowcount or 0

    if dead_strings:
        await _deactivate_in_all_instances(list(dead_strings))
    return n


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


async def edit_central_proxy(proxy_id: int, new_raw: str) -> dict:
    """
    ✏️ ویرایش پروکسی استخر مرکزی — روی «همه» اینستنس‌هایی که آن را دارند اثر می‌گذارد:
      • رشته جدید در انبار موجود است → ادغام (تخصیص‌ها به آن منتقل، قدیمی حذف)
      • در غیر این صورت → همان ردیف درجا به‌روزرسانی می‌شود (رشته قدیمی در همه
        دیتابیس‌های اینستنس غیرفعال و رشته جدید در سیکل بعد فعال می‌شود)
    """
    from utils.proxy_parse import parse_proxy_line
    p = parse_proxy_line(new_raw.strip())
    if not p:
        return {"ok": False, "msg": "❌ فرمت پروکسی جدید نامعتبر است.\n"
                "مثال: <code>socks5://user:pass@host:port</code> یا <code>host:port:user:pass</code>"}

    async with async_session() as session:
        old = await session.get(FactoryProxy, int(proxy_id))
        if not old:
            return {"ok": False, "msg": "پروکسی یافت نشد."}
        old_string = old.proxy_string
        if old_string == p.proxy_string:
            return {"ok": False, "msg": "ℹ️ رشته جدید با مقدار فعلی یکسان است."}

        # اگر رشته جدید قبلاً در انبار هست → ادغام
        existing = (await session.scalars(
            select(FactoryProxy).where(FactoryProxy.proxy_string == p.proxy_string)
        )).first()
        if existing and existing.id != old.id:
            # اینستنس‌هایی که هر دو پروکسی را دارند → تخصیص موجود به both ارتقا می‌یابد
            conflict_oids = set((await session.scalars(
                select(InstanceProxy.order_id).where(InstanceProxy.proxy_id == old.id)
            )).all()) & set((await session.scalars(
                select(InstanceProxy.order_id).where(InstanceProxy.proxy_id == existing.id)
            )).all())
            for oid in conflict_oids:
                keep = (await session.scalars(
                    select(InstanceProxy).where(
                        InstanceProxy.order_id == oid,
                        InstanceProxy.proxy_id == existing.id)
                )).first()
                if keep:
                    keep.usage_type = "both"
                    keep.enabled = True
            # حذف تخصیص‌های قدیمیِ متعارض + انتقال بقیه به پروکسی جدید
            if conflict_oids:
                await session.execute(
                    delete(InstanceProxy).where(
                        InstanceProxy.proxy_id == old.id,
                        InstanceProxy.order_id.in_(list(conflict_oids))))
            await session.execute(
                update(InstanceProxy).where(InstanceProxy.proxy_id == old.id)
                .values(proxy_id=existing.id))
            await session.delete(old)
            await session.commit()
        else:
            old.scheme = p.scheme
            old.host = p.host
            old.port = p.port
            old.username = p.username
            old.password = p.password
            old.proxy_string = p.proxy_string
            old.status = "active"
            old.ping_ms = None
            old.consec_ok = 0
            old.consec_fail = 0
            old.last_synced_at = None
            await session.commit()

    # رشته قدیمی را در همه دیتابیس‌های اینستنس غیرفعال کن (جایگزین شده است)
    await _deactivate_in_all_instances([old_string])

    t = asyncio.create_task(check_cycle(force=True))
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return {"ok": True, "msg": f"✏️ پروکسی ویرایش شد: <code>{mask_proxy(p.proxy_string)}</code>\n"
            f"رشته قدیمی <code>{mask_proxy(old_string)}</code> از همه اینستنس‌ها خارج شد؛ "
            f"تست فوری و سینک در جریان است."}


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
                    transitions.append((pd["proxy_string"], old, new))
                
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

        # اعلان ادمین (آگاه از اینستنس‌های متأثر — برای پروکسی‌های تخصیص‌یافته)
        if transitions:
            # نقشه رشته پروکسی → اینستنس‌های متأثر (فقط تخصیص‌های روشن)
            async with async_session() as session:
                usage_rows = (await session.execute(
                    select(InstanceProxy, FactoryProxy, Order)
                    .join(FactoryProxy, FactoryProxy.id == InstanceProxy.proxy_id)
                    .join(Order, Order.id == InstanceProxy.order_id)
                    .where(InstanceProxy.enabled == True,
                           Order.status.in_("deployed", "stopped"))
                )).all()
            usage_map = {}
            for ap, fp, o in usage_rows:
                t = "لاگین" if ap.usage_type == "login" else "سندر" if ap.usage_type == "sender" else "هردو"
                usage_map.setdefault(fp.proxy_string, []).append(f"bot_{o.id}({t})")

            lines = ["🌐 <b>تغییر وضعیت پروکسی‌ها:</b>"]
            for ps, old, new in transitions[:15]:
                icon = {"active": "🟢", "weak": "🟡", "dead": "🔴"}.get(new, "⚪")
                line = f"{icon} <code>{mask_proxy(ps)}</code> : {old} → <b>{new}</b>"
                affected = usage_map.get(ps, [])
                if affected and new == "dead":
                    line += "\n     └ ⚠️ متأثر: " + ", ".join(affected[:8])
                    if len(affected) > 8:
                        line += f" +{len(affected) - 8} مورد"
                lines.append(line)
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

        return {"checked": len(proxies_data),
                "transitions": [(mask_proxy(ps), old, new) for ps, old, new in transitions],
                "synced": synced}

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
                "usage_type": config.PROXY_USAGE_TYPE,
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
            "usage_type": config.PROXY_USAGE_TYPE,
        }
        for p in picked
    }


def _desired_from_assignments(rows: list) -> dict:
    """
    🎯 ساخت desired map از تخصیص‌های اختصاصی اینستنس (جدول instance_proxies):
    فقط تخصیص‌های «روشن» با پروکسی سالم (active/weak) — هرکدام با usage_type خودش.
    """
    desired = {}
    for ap, fp in rows:
        if not ap.enabled or fp.status not in ("active", "weak"):
            continue
        desired[fp.proxy_string] = {
            "health_state": "HEALTHY" if fp.status == "active" else "WEAK",
            "is_healthy": 1 if fp.status == "active" else 0,
            "ping_ms": fp.ping_ms,
            "usage_type": ap.usage_type if ap.usage_type in ("login", "sender", "both") else config.PROXY_USAGE_TYPE,
        }
    return desired


async def _resolve_desired(order_id: int, healthy_rows: list) -> dict:
    """
    منبع واحد تصمیم سینک برای هر اینستنس:
      • تخصیص اختصاصی دارد → فقط پروکسی‌های تخصیص‌یافته روشن و سالم (با نوع خودشان)
      • هیچ تخصیصی ندارد → حالت legacy: چرخش عمومی استخر سالم (رفتار نسخه قبل)
    """
    async with async_session() as session:
        rows = (await session.execute(
            select(InstanceProxy, FactoryProxy)
            .join(FactoryProxy, FactoryProxy.id == InstanceProxy.proxy_id)
            .where(InstanceProxy.order_id == int(order_id))
        )).all()
    if rows:
        return _desired_from_assignments(rows)
    return _desired_for_instance(order_id, healthy_rows)

# ------------------------------------------------------------
# سینک به اینستنس‌ها (قسمت لاگین + سندر)
# ------------------------------------------------------------
async def sync_all_instances(reason: str = "") -> int:
    """
    نوشتن پروکسی‌های «مخصوص هر اینستنس» در جدول proxies همان اینستنس:
      • اینستنس با تخصیص اختصاصی → پروکسی‌های تخصیص‌یافته روشن و سالم با usage_type خودشان
        (login/sender/both — سندرها دقیقاً همان‌هایی هستند که claim سندر برمی‌دارد)
      • اینستنس بدون تخصیص → legacy چرخش عمومی استخر سالم
    • درج/به‌روزرسانی: is_active=1, health_state=HEALTHY|WEAK, ping_ms
    • سطرهای ناموجود در desired (مرده/حذف‌شده/غیرتخصیصی): is_active=0, health_state=DEAD
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

            # 🛡 گارد ایمنی: استخر سالم کاملاً خالی = احتمالاً مشکل شبکه خود فکتوری؛
            # سینک نکن تا ردیف‌های اینستنس‌ها به اشتباه خاموش نشوند (رفتار نسخه قبل).
            if not rows:
                logger.warning("استخر پروکسی‌های سالم فکتوری خالی است. سینک متوقف شد.")
                await _notify(
                    "⚠️ <b>هشدار فکتوری:</b> لیست پروکسی‌های سالم کاملاً خالی است. "
                    "برای جلوگیری از قطعی لاگین/سندر اینستنس‌ها، عملیات سینک لغو شد. "
                    "از /proxies وضعیت استخر را بررسی کنید."
                )
                return 0

            orders = (await session.scalars(
                select(Order).where(Order.status.in_(("deployed", "stopped")))
            )).all()
            order_ids = [o.id for o in orders]

        ok_count = 0
        total_synced_proxies = 0
        for oid in order_ids:
            ok = True
            desired = await _resolve_desired(int(oid), rows)
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

    await sync_instance_now(order_id)


async def sync_instance_now(order_id: int) -> int:
    """
    🎯 سینک فوری «فقط همین اینستنس» (بدون انتظار برای جدول‌ها):
    پروکسی‌های تخصیص‌یافته روشن و سالم با usage_type خودشان نوشته می‌شود؛
    بقیه ردیف‌های مدیریت‌شده غیرفعال می‌شوند. برای دکمه‌های پنل هر اینستنس.
    """
    order_id = int(order_id)
    async with async_session() as session:
        rows = (await session.scalars(
            select(FactoryProxy)
            .where(FactoryProxy.status.in_(("active", "weak")))
            .order_by(FactoryProxy.id)
        )).all()
        desired = await _resolve_desired(order_id, rows)

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
        return len(desired)
    except Exception as e:
        logger.error(f"Error syncing {order_id}: {e}")
        return -1


async def _sync_one_instance(order_id: int, desired: dict) -> None:
    """
    نوشتن desired (پروکسی + usage_type مخصوص هر اینستنس) در جدول proxies اینستنس:
      • INSERT … ON DUPLICATE KEY — usage_type جدید فقط وقتی می‌نشیند که قبلی 'both'
        نباشد؛ 'both' (تنظیم دستی مشتری یا تخصیص هردو) همیشه حفظ می‌شود.
      • غیرفعال‌سازی ردیف‌های مدیریت‌شده‌ای که دیگر در desired نیستند
        (مرده / حذف تخصیص / تخصیص خاموش) — پروکسی شخصی مشتری هرگز دست نمی‌خورد.
    """
    db_name = config.db_name_for(order_id)
    from database.engine import get_shared_app_engine
    eng = get_shared_app_engine()

    from sqlalchemy import text, select
    from database.models import FactoryProxy, InstanceProxy

    # رشته‌های تحت مدیریت فکتوری برای این اینستنس:
    #   الف) همه پروکسی‌های انبار مرکزی +  ب) رشته‌های تخصیص‌یافته (حتی اگر پروکسی
    #   از انبار حذف شده باشد) — تا هیچ ردیف مدیریتی سرگردان فعلی باقی نماند.
    async with async_session() as session:
        known_to_factory = set((await session.scalars(select(FactoryProxy.proxy_string))).all())
        assigned = (await session.execute(
            select(FactoryProxy.proxy_string)
            .join(InstanceProxy, InstanceProxy.proxy_id == FactoryProxy.id)
            .where(InstanceProxy.order_id == int(order_id))
        )).all()
        managed = known_to_factory | {r[0] for r in assigned}

    async with eng.begin() as conn:
        # ۱) وضعیت فعلی اینستنس (همه انواع — سندر هم مدیریت می‌شود)
        current = dict(
            (await conn.execute(text(
                f"SELECT proxy_string, is_active FROM `{db_name}`.`proxies` WHERE usage_type IN ('login','sender','both')"
            ))).all()
        )

        # ۲) درج/به‌روزرسانی پروکسی‌های سالم (با usage_type مخصوص همین اینستنس)
        if desired:
            await conn.execute(
                text(
                    f"INSERT INTO `{db_name}`.`proxies` "
                    "(proxy_string, is_active, usage_type, is_healthy, health_state, ping_ms, last_checked_at) "
                    "VALUES (:ps, 1, :ut, :ih, :hs, :pm, NOW()) "
                    "ON DUPLICATE KEY UPDATE "
                    "  is_active=1, "
                    "  usage_type=IF(VALUES(usage_type)='both' OR usage_type='both', 'both', VALUES(usage_type)), "
                    "  is_healthy=VALUES(is_healthy), "
                    "  health_state=VALUES(health_state), ping_ms=VALUES(ping_ms), "
                    "  last_checked_at=NOW()"
                ),
                [
                    {
                        "ps": ps, "ut": v.get("usage_type", config.PROXY_USAGE_TYPE),
                        "ih": v["is_healthy"], "hs": v["health_state"], "pm": v["ping_ms"],
                    }
                    for ps, v in desired.items()
                ],
            )

        # ۳) غیرفعال‌سازی سطرهای مدیریتی که در desired نیستند
        #    (شرط ps in managed تضمین می‌کند پروکسی شخصی کاربر را دستکاری نکنیم)
        to_disable = [
            ps for ps, act in current.items()
            if act and ps in managed and ps not in desired
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

        # 🎯 آمار لایه تخصیص (مدیریت تفکیک‌شده هر اینستنس)
        total_assignments = await session.scalar(
            select(func.count(InstanceProxy.id))) or 0
        enabled_assignments = await session.scalar(
            select(func.count(InstanceProxy.id)).where(InstanceProxy.enabled == True)) or 0
        inst_with_assign = await session.scalar(
            select(func.count(func.distinct(InstanceProxy.order_id)))
        ) or 0
        
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
        "assigned": {
            "total": int(total_assignments),
            "enabled": int(enabled_assignments),
            "instances": int(inst_with_assign),
        },
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