"""
🎯 Instance Proxy Manager — موتور مدیریت پروکسی تفکیک‌شده هر اینستنس
=====================================================================
خواسته کارفرما:
  «خودم می‌خواهم از طریق ربات‌ساز برای هر اینستنس، پروکسی لاگین و سندر اضافه کنم
   و کاربر اصلاً دخلی نداشته باشد — یعنی خودم بتوانم پروکسی اضافه کنم، حذف کنم،
   ادیت بزنم، تست بگیرم و هر چیز لازم برای یک سیستم پیشرفته و جامع.»

معماری (لایه تخصیص):
  ┌──────────────────────────────────────────────────────────────┐
  │ factory_proxies (استخر مرکزی = انبار پروکسی + تست مداوم)      │
  └──────────────┬───────────────────────────────────────────────┘
                 │ instance_proxies (جدول تخصیص: order_id + proxy_id + usage_type)
                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ سینک اختصاصی: هر اینستنس فقط پروکسی‌های خودش را در جدول       │
  │ proxies دیتابیس خودش می‌گیرد (با usage_type مخصوص خودش)       │
  └──────────────────────────────────────────────────────────────┘

نوع استفاده (usage_type) — دقیقاً مطابق سورس sender_bot:
  • login  → فقط برای لاگین اکانت‌ها (login_handlers: usage_type IN ('login','both'))
  • sender → فقط برای ارسال (claim_proxy_for_account: usage_type IN ('sender','both'))
  • both   → هردو مسیر
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select, update, delete, func, text, case

from config import config
from database import async_session
from database.models import FactoryProxy, InstanceProxy, Order
from utils.proxy_parse import parse_proxies_text, parse_proxy_line, mask_proxy

logger = logging.getLogger(__name__)

VALID_TYPES = ("login", "sender", "both")
TYPE_LABEL = {"login": "🔑 لاگین", "sender": "📤 سندر", "both": "♻️ هردو"}

_bg_tasks = set()
_notifier = None


def set_notifier(fn) -> None:
    global _notifier
    _notifier = fn


async def notify(text: str) -> None:
    if _notifier is None:
        return
    try:
        await _notifier(text)
    except Exception:
        logger.exception("ارسال اعلان پروکسی اینستنس ناموفق")


def _spawn(coro):
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


# ============================================================
# ابزار کمکی
# ============================================================
def _norm_type(t: str) -> str:
    t = (t or "").strip().lower()
    if t in ("send", "sender", "out", "ارسال", "سندر"):
        return "sender"
    if t in ("both", "all", "هردو"):
        return "both"
    return "login"


async def _get_order(order_id: int) -> Order | None:
    async with async_session() as session:
        return await session.get(Order, int(order_id))


async def count_for_order(order_id: int) -> int:
    async with async_session() as session:
        return int(await session.scalar(
            select(func.count(InstanceProxy.id)).where(InstanceProxy.order_id == int(order_id))
        ) or 0)


async def summary_for_orders() -> dict:
    """خلاصه تعداد تخصیص به ازای هر order_id — برای لیست انتخاب اینستنس."""
    async with async_session() as session:
        rows = (await session.execute(
            select(InstanceProxy.order_id,
                   func.sum(case((InstanceProxy.usage_type.in_(("login", "both")), 1), else_=0)),
                   func.sum(case((InstanceProxy.usage_type.in_(("sender", "both")), 1), else_=0)))
            .where(InstanceProxy.enabled == True)
            .group_by(InstanceProxy.order_id)
        )).all()
    return {int(r[0]): {"login": int(r[1] or 0), "sender": int(r[2] or 0)} for r in rows}


# ============================================================
# افزودن پروکسی مستقیم به اینستنس (متن چندخطی)
# ============================================================
async def assign_text_to_instance(order_id: int, raw_text: str, usage_type: str) -> dict:
    """
    افزودن پروکسی برای یک اینستنس خاص:
      ۱) پارس چندخطی (فرمت‌های socks5://… ، host:port:user:pass ، host:port)
      ۲) افزودن به استخر مرکزی (اگر جدید باشد — انبار واحد)
      ۳) ثبت/ارتقای تخصیص در instance_proxies (اگر از قبل با نوع دیگر تخصیص
         داشته باشد → ارتقا به both)
    خروجی: آمار کامل برای گزارش ادمین.
    """
    order_id = int(order_id)
    usage_type = _norm_type(usage_type)
    parsed, invalid = parse_proxies_text(raw_text)

    if not parsed and not invalid:
        return {"ok": False, "msg": "هیچ خط قابل شناسایی نبود."}

    added = 0          # تخصیص جدید
    upgraded = 0       # تخصیص موجود که به both ارتقا یافت
    duplicate = 0      # همین نوع از قبل تخصیص داشت
    pool_new = 0       # پروکسی جدید در استخر مرکزی
    invalid_lines = invalid[:5]

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order or order.status not in ("deployed", "stopped", "provisioning"):
            return {"ok": False, "msg": "اینستنس یافت نشد یا فعال نیست."}

        for p in parsed:
            # ۱) انبار مرکزی (بدون تکرار)
            fp = (await session.scalars(
                select(FactoryProxy).where(FactoryProxy.proxy_string == p.proxy_string)
            )).first()
            if not fp:
                fp = FactoryProxy(
                    scheme=p.scheme, host=p.host, port=p.port,
                    username=p.username, password=p.password,
                    proxy_string=p.proxy_string, status="active",
                )
                session.add(fp)
                await session.flush()
                pool_new += 1

            # ۲) تخصیص به اینستنس
            exist = (await session.scalars(
                select(InstanceProxy).where(
                    InstanceProxy.order_id == order_id,
                    InstanceProxy.proxy_id == fp.id,
                )
            )).first()

            if exist:
                if exist.usage_type == usage_type:
                    if not exist.enabled:
                        exist.enabled = True
                        added += 1
                    else:
                        duplicate += 1
                elif {exist.usage_type, usage_type} == {"login", "sender"}:
                    exist.usage_type = "both"
                    exist.enabled = True
                    upgraded += 1
                else:
                    # یکی از دو طرف both است → همان both می‌ماند
                    exist.usage_type = "both"
                    exist.enabled = True
                    duplicate += 1
            else:
                session.add(InstanceProxy(
                    order_id=order_id, proxy_id=fp.id,
                    usage_type=usage_type, enabled=True,
                ))
                added += 1
        await session.commit()

    # تست فوری همان لحظه + سینک اختصاصی همین اینستنس
    if added or upgraded or pool_new:
        async def _post():
            try:
                from core.proxy_manager import check_cycle, sync_instance_now
                await check_cycle(force=True)
                await sync_instance_now(order_id)
            except Exception:
                logger.exception("پس‌زمینه تست/سینک پس از تخصیص ناموفق")
        _spawn(_post())

    msg = (f"✅ <b>نتیجه افزودن به bot_{order_id}</b> ({TYPE_LABEL[usage_type]})\n"
           f"🆕 تخصیص جدید: {added} | ♻️ ارتقا به هردو: {upgraded}"
           f" | ⏭ از قبل بود: {duplicate}\n"
           f"📥 پروکسی جدید در استخر مرکزی: {pool_new} | ❌ نامعتبر: {len(invalid)}")
    return {"ok": True, "msg": msg, "invalid_lines": invalid_lines,
            "added": added, "upgraded": upgraded, "duplicate": duplicate}


# ============================================================
# تخصیص از استخر مرکزی (بهترین‌ها با توزیع بار)
# ============================================================
async def assign_from_pool(order_id: int, count: int, usage_type: str) -> dict:
    """
    انتخاب بهترین پروکسی‌های استخر مرکزی برای اینستنس:
      • مرتب‌سازی: اول سالم (active)، بعد کمترین تعداد تخصیص سراسری (توزیع بار)، بعد پینگ
      • اگر همان پروکسی از قبل با نوع دیگر تخصیص دارد → ارتقا به both
    """
    order_id = int(order_id)
    usage_type = _norm_type(usage_type)
    count = max(0, int(count))

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order or order.status not in ("deployed", "stopped", "provisioning"):
            return {"ok": False, "msg": "اینستنس یافت نشد یا فعال نیست."}

        # پروکسی‌های سالم استخر + تعداد تخصیص فعلی هرکدام (توزیع بار)
        load_sub = (
            select(func.count(InstanceProxy.id))
            .where(InstanceProxy.proxy_id == FactoryProxy.id, InstanceProxy.enabled == True)
            .correlate(FactoryProxy)
            .scalar_subquery()
        )
        rows = (await session.scalars(
            select(FactoryProxy)
            .where(FactoryProxy.status.in_(("active", "weak")))
            .order_by(
                load_sub.asc(),                      # توزیع بار: اول پروکسی‌هایی که اینستنس‌های کمتری دارند
                FactoryProxy.status != "active",      # active قبل از weak
                FactoryProxy.ping_ms.is_(None),
                FactoryProxy.ping_ms.asc(),
            )
        )).all()

        if not rows:
            return {"ok": False, "msg": "⚠️ استخر مرکزی پروکسی سالم ندارد. اول از /proxies پروکسی اضافه کنید."}

        # تخصیص‌های فعلی اینستنس
        current = {
            ap.proxy_id: ap for ap in (await session.scalars(
                select(InstanceProxy).where(InstanceProxy.order_id == order_id)
            )).all()
        }

        assigned_new = upgraded = 0
        for fp in rows:
            if assigned_new + upgraded >= count:
                break
            ap = current.get(fp.id)
            if ap:
                if ap.usage_type == usage_type:
                    continue
                if {ap.usage_type, usage_type} == {"login", "sender"} or "both" in (ap.usage_type, usage_type):
                    ap.usage_type = "both"
                    ap.enabled = True
                    upgraded += 1
            else:
                session.add(InstanceProxy(
                    order_id=order_id, proxy_id=fp.id,
                    usage_type=usage_type, enabled=True,
                ))
                assigned_new += 1
        await session.commit()

    total_now = await count_for_order(order_id)
    if assigned_new or upgraded:
        async def _post():
            try:
                from core.proxy_manager import sync_instance_now
                await sync_instance_now(order_id)
            except Exception:
                logger.exception("سینک پس از تخصیص از استخر ناموفق")
        _spawn(_post())

    return {"ok": True, "msg": (
        f"🎯 از استخر مرکزی به bot_{order_id} تخصیص یافت:\n"
        f"🆕 جدید: {assigned_new} | ♻️ ارتقا به هردو: {upgraded}\n"
        f"📦 کل تخصیص‌های این اینستنس: {total_now}"
    )}


# ============================================================
# تخصیص خودکار موقع ساخت اینستنس (کارفرما: کاربر دخالت نداشته باشد)
# ============================================================
async def auto_assign_for_new_instance(order_id: int) -> dict:
    """
    بعد از provision موفق: N پروکسی لاگین + M پروکسی سندر بهترینِ استخر
    به‌صورت خودکار تخصیص می‌یابد. اگر استخر کم داشته باشد هشدار ادمین می‌رود.
    (اگر اینستنس از قبل تخصیص دارد — مثلاً re-provision — دست نمی‌زند.)
    """
    order_id = int(order_id)
    if not config.AUTO_ASSIGN_ON_PROVISION:
        return {"ok": True, "skipped": "off"}

    if await count_for_order(order_id) > 0:
        return {"ok": True, "skipped": "existing"}

    n_login = max(0, config.AUTO_ASSIGN_LOGIN_COUNT)
    n_sender = max(0, config.AUTO_ASSIGN_SENDER_COUNT)

    async with async_session() as session:
        healthy = int(await session.scalar(
            select(func.count(FactoryProxy.id)).where(FactoryProxy.status.in_(("active", "weak")))
        ) or 0)

    if healthy == 0:
        await notify(
            f"⚠️ <b>bot_{order_id} بدون پروکسی ماند!</b>\n"
            f"استخر مرکزی پروکسی سالم ندارد — از /proxies پروکسی اضافه کنید و بعد "
            f"از پنل اینستنس تخصیص بدهید."
        )
        return {"ok": False, "msg": "empty-pool"}

    results = []
    if n_login > 0:
        r = await assign_from_pool(order_id, n_login, "login")
        results.append(f"لاگین: {r.get('msg', '')}")
    if n_sender > 0:
        r = await assign_from_pool(order_id, n_sender, "sender")
        results.append(f"سندر: {r.get('msg', '')}")

    # هشدار کمبود
    st = await instance_stats(order_id)
    healthy_login = st["login"]["healthy"] + st["both"]["healthy"]
    healthy_sender = st["sender"]["healthy"] + st["both"]["healthy"]
    warn = ""
    if healthy_login < config.INSTANCE_PROXY_MIN_HEALTHY or healthy_sender < config.INSTANCE_PROXY_MIN_HEALTHY:
        warn = (f"\n⚠️ سهمیه سالم کم است (لاگین: {healthy_login} | سندر: {healthy_sender} — "
                f"حداقل پیشنهادی: {config.INSTANCE_PROXY_MIN_HEALTHY})")

    await notify(
        f"🎯 <b>تخصیص خودکار پروکسی به bot_{order_id}</b>\n" +
        "\n".join(results) + warn
    )
    return {"ok": True, "msg": "\n".join(results) + warn}


# ============================================================
# حذف / خاموش‌و‌روشن / تغییر نوع
# ============================================================
async def unassign(assignment_id: int, resync: bool = True) -> dict:
    """حذف تخصیص یک پروکسی از یک اینستنس (پروکسی در استخر مرکزی می‌ماند)."""
    async with async_session() as session:
        ap = await session.get(InstanceProxy, int(assignment_id))
        if not ap:
            return {"ok": False, "msg": "تخصیص یافت نشد."}
        order_id, proxy_id, utype = ap.order_id, ap.proxy_id, ap.usage_type
        await session.delete(ap)
        await session.commit()

    if resync:
        async def _post():
            try:
                from core.proxy_manager import sync_instance_now
                await sync_instance_now(order_id)
            except Exception:
                logger.exception("سینک پس از حذف تخصیص ناموفق")
        _spawn(_post())
    return {"ok": True, "msg": f"🗑 تخصیص #{assignment_id} ({TYPE_LABEL[utype]}) از bot_{order_id} حذف شد.",
            "order_id": order_id}


async def unassign_all(order_id: int, only_dead: bool = False) -> dict:
    """حذف همه تخصیص‌های یک اینستنس (یا فقط تخصیص‌های پروکسی مرده)."""
    order_id = int(order_id)
    async with async_session() as session:
        q = select(InstanceProxy).where(InstanceProxy.order_id == order_id)
        if only_dead:
            q = q.join(FactoryProxy, FactoryProxy.id == InstanceProxy.proxy_id)\
                 .where(FactoryProxy.status == "dead")
        rows = (await session.scalars(q)).all()
        for ap in rows:
            await session.delete(ap)
        await session.commit()
        n = len(rows)

    if n:
        async def _post():
            try:
                from core.proxy_manager import sync_instance_now
                await sync_instance_now(order_id)
            except Exception:
                logger.exception("سینک پس از حذف گروهی ناموفق")
        _spawn(_post())
    return {"ok": True, "msg": f"🗑 {n} تخصیص از bot_{order_id} حذف شد." +
            (" (فقط مرده‌ها)" if only_dead else "")}


async def set_enabled(assignment_id: int, enabled: bool) -> dict:
    """روشن/خاموش تخصیص بدون حذف — پروکسی از اینستنس خارج/برمی‌گردد."""
    async with async_session() as session:
        ap = await session.get(InstanceProxy, int(assignment_id))
        if not ap:
            return {"ok": False, "msg": "تخصیص یافت نشد."}
        ap.enabled = bool(enabled)
        order_id = ap.order_id
        await session.commit()

    async def _post():
        try:
            from core.proxy_manager import sync_instance_now
            await sync_instance_now(order_id)
        except Exception:
            logger.exception("سینک پس از تغییر فعال‌سازی ناموفق")
    _spawn(_post())
    return {"ok": True, "msg": ("🟢 تخصیص فعال شد." if enabled else "⚪ تخصیص غیرفعال شد."), "order_id": order_id}


async def set_usage_type(assignment_id: int, new_type: str) -> dict:
    """تغییر نوع استفاده تخصیص (لاگین/سندر/هردو)."""
    new_type = _norm_type(new_type)
    async with async_session() as session:
        ap = await session.get(InstanceProxy, int(assignment_id))
        if not ap:
            return {"ok": False, "msg": "تخصیص یافت نشد."}
        ap.usage_type = new_type
        order_id = ap.order_id
        await session.commit()

    async def _post():
        try:
            from core.proxy_manager import sync_instance_now
            await sync_instance_now(order_id)
        except Exception:
            logger.exception("سینک پس از تغییر نوع ناموفق")
    _spawn(_post())
    return {"ok": True, "msg": f"🔀 نوع تخصیص به {TYPE_LABEL[new_type]} تغییر یافت.", "order_id": order_id}


async def edit_assignment_proxy(assignment_id: int, new_raw: str) -> dict:
    """
    ✏️ ویرایش آدرس/اعتبارنامه پروکسی از پنل اینستنس:
    اگر رشته جدید با قبلی یکی باشد فقط گزارش؛ در غیر این صورت:
      • پروکسی جدید در انبار مرکزی ثبت (یا موجود پیدا) می‌شود
      • تخصیص از پروکسی قدیم به جدید re-point می‌شود (نوع و وضعیت حفظ می‌شود)
      • سینک اختصاصی: رشته قدیمی در اینستنس deactive و جدید فعال می‌شود
    (سایر اینستنس‌هایی که پروکسی قدیم را دارند دست‌نخورده می‌مانند.)
    """
    p = parse_proxy_line(new_raw.strip())
    if not p:
        return {"ok": False, "msg": "❌ فرمت پروکسی جدید نامعتبر است.\n"
                "مثال: <code>socks5://user:pass@host:port</code> یا <code>host:port:user:pass</code>"}

    async with async_session() as session:
        ap = await session.get(InstanceProxy, int(assignment_id))
        if not ap:
            return {"ok": False, "msg": "تخصیص یافت نشد."}
        old_fp = await session.get(FactoryProxy, ap.proxy_id)
        if old_fp and old_fp.proxy_string == p.proxy_string:
            return {"ok": False, "msg": "ℹ️ رشته جدید با مقدار فعلی یکسان است؛ تغییری لازم نیست."}

        # پروکسی جدید در انبار
        new_fp = (await session.scalars(
            select(FactoryProxy).where(FactoryProxy.proxy_string == p.proxy_string)
        )).first()
        if not new_fp:
            new_fp = FactoryProxy(
                scheme=p.scheme, host=p.host, port=p.port,
                username=p.username, password=p.password,
                proxy_string=p.proxy_string, status="active",
            )
            session.add(new_fp)
            await session.flush()

        # اگر همین اینستنس از قبل پروکسی جدید را هم دارد → ادغام (ارتقا به both)
        other = (await session.scalars(
            select(InstanceProxy).where(
                InstanceProxy.order_id == ap.order_id,
                InstanceProxy.proxy_id == new_fp.id,
                InstanceProxy.id != ap.id,
            )
        )).first()
        if other:
            other.usage_type = "both"
            other.enabled = True
            await session.delete(ap)
            order_id = ap.order_id
            await session.commit()
        else:
            ap.proxy_id = new_fp.id
            ap.enabled = True
            order_id = ap.order_id
            await session.commit()

    async def _post():
        try:
            from core.proxy_manager import check_cycle, sync_instance_now
            await check_cycle(force=True)
            await sync_instance_now(order_id)
        except Exception:
            logger.exception("تست/سینک پس از ویرایش ناموفق")
    _spawn(_post())
    return {"ok": True, "msg": f"✏️ پروکسی ویرایش شد و به bot_{order_id} اعمال گشت "
            f"(<code>{mask_proxy(p.proxy_string)}</code>) — تست فوری در جریان است."}


# ============================================================
# خواندن وضعیت اینستنس
# ============================================================
async def instance_stats(order_id: int) -> dict:
    """آمار تخصیص‌های یک اینستنس + وضعیت سلامت هر گروه."""
    order_id = int(order_id)
    async with async_session() as session:
        rows = (await session.execute(
            select(InstanceProxy, FactoryProxy)
            .join(FactoryProxy, FactoryProxy.id == InstanceProxy.proxy_id)
            .where(InstanceProxy.order_id == order_id)
        )).all()

    def bucket():
        return {"total": 0, "enabled": 0, "healthy": 0, "weak": 0, "dead": 0, "unknown": 0}

    out = {
        "login": bucket(), "sender": bucket(), "both": bucket(),
        "total": 0, "enabled": 0, "dead_assignments": 0, "mode": "legacy",
    }
    for ap, fp in rows:
        out["total"] += 1
        if ap.enabled:
            out["enabled"] += 1
        grp = out[ap.usage_type if ap.usage_type in VALID_TYPES else "login"]
        grp["total"] += 1
        if ap.enabled:
            grp["enabled"] += 1
            if fp.status == "active":
                grp["healthy"] += 1
            elif fp.status == "weak":
                grp["weak"] += 1
            elif fp.status == "dead":
                grp["dead"] += 1
            else:
                grp["unknown"] += 1
            if fp.status == "dead":
                out["dead_assignments"] += 1
    if out["total"] > 0:
        out["mode"] = "per-instance"
    return out


async def instance_list(order_id: int, page: int = 1, per_page: int = 8) -> dict:
    """لیست صفحه‌بندی‌شده تخصیص‌های اینستنس برای پنل ادمین."""
    order_id = int(order_id)
    async with async_session() as session:
        q = (select(InstanceProxy, FactoryProxy)
             .join(FactoryProxy, FactoryProxy.id == InstanceProxy.proxy_id)
             .where(InstanceProxy.order_id == order_id)
             .order_by(InstanceProxy.id))
        total = int(await session.scalar(
            select(func.count()).select_from(q.subquery())
        ) or 0)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        rows = (await session.execute(q.offset((page - 1) * per_page).limit(per_page))).all()
    return {"rows": rows, "total": total, "page": page, "total_pages": total_pages}


async def get_assignment(assignment_id: int) -> dict | None:
    """جزئیات یک تخصیص + وضعیت همان پروکسی در دیتابیس اینستنس (in_use و …)."""
    async with async_session() as session:
        row = (await session.execute(
            select(InstanceProxy, FactoryProxy, Order)
            .join(FactoryProxy, FactoryProxy.id == InstanceProxy.proxy_id)
            .join(Order, Order.id == InstanceProxy.order_id)
            .where(InstanceProxy.id == int(assignment_id))
        )).first()
    if not row:
        return None
    ap, fp, order = row

    # وضعیت در دیتابیس اینستنس (best-effort — ممکن است جدول هنوز ساخته نشده باشد)
    inst_info = None
    try:
        from database.engine import get_shared_app_engine
        eng = get_shared_app_engine()
        db_name = config.db_name_for(ap.order_id)
        async with eng.connect() as conn:
            res = await conn.execute(text(
                f"SELECT is_active, usage_type, health_state, ping_ms, in_use, fail_count "
                f"FROM `{db_name}`.`proxies` WHERE proxy_string = :ps LIMIT 1"
            ), {"ps": fp.proxy_string})
            r = res.first()
            if r:
                inst_info = {"is_active": bool(r[0]), "usage_type": r[1],
                             "health_state": r[2], "ping_ms": r[3],
                             "in_use": int(r[4] or 0), "fail_count": int(r[5] or 0)}
    except Exception:
        pass

    return {"ap": ap, "fp": fp, "order": order, "inst_info": inst_info}


async def export_instance(order_id: int) -> str:
    """خروجی متنی تخصیص‌های یک اینستنس (برای بکاپ/انتقال)."""
    order_id = int(order_id)
    async with async_session() as session:
        rows = (await session.execute(
            select(InstanceProxy, FactoryProxy)
            .join(FactoryProxy, FactoryProxy.id == InstanceProxy.proxy_id)
            .where(InstanceProxy.order_id == order_id)
            .order_by(InstanceProxy.id)
        )).all()
    if not rows:
        return f"(bot_{order_id} هیچ تخصیص پروکسی ندارد)"
    lines = [f"# bot_{order_id} proxies  (format: scheme://user:pass@host:port  # type status ping)"]
    for ap, fp in rows:
        url = fp.proxy_string if "@" in fp.proxy_string else fp.proxy_string
        lines.append(
            f"{fp.scheme}://{url}  # {ap.usage_type}{' on' if ap.enabled else ' off'}"
            f" {fp.status} {fp.ping_ms or '-'}ms"
        )
    return "\n".join(lines)


# ============================================================
# تست زنده پروکسی‌های یک اینستنس
# ============================================================
async def test_instance_proxies(order_id: int) -> dict:
    """
    تست زنده همه پروکسی‌های تخصیص‌یافته یک اینستنس (اتصال از طریق خود پروکسی به DC تلگرام).
    وضعیت استخر مرکزی هم بلافاصله به‌روز می‌شود (ماشین وضعیت همان check_cycle).
    """
    from core.proxy_manager import _check_one
    order_id = int(order_id)

    async with async_session() as session:
        rows = (await session.execute(
            select(FactoryProxy)
            .join(InstanceProxy, InstanceProxy.proxy_id == FactoryProxy.id)
            .where(InstanceProxy.order_id == order_id)
        )).all()
        data = [{"id": p.id, "scheme": p.scheme, "host": p.host, "port": p.port,
                 "username": p.username, "password": p.password,
                 "proxy_string": p.proxy_string, "status": p.status}
                for p in rows]

    if not data:
        return {"ok": False, "msg": "اینستنس پروکسی تخصیص‌یافته ندارد."}

    sem = asyncio.Semaphore(max(1, config.PROXY_CONCURRENCY))

    async def guarded(pd):
        async with sem:
            from core.proxy_manager import _check_one
            temp = FactoryProxy(scheme=pd["scheme"], host=pd["host"], port=pd["port"],
                                username=pd["username"], password=pd["password"])
            return await _check_one(temp)

    results = await asyncio.gather(*[guarded(pd) for pd in data], return_exceptions=True)

    # اعمال نتایج روی استخر مرکزی (به‌روزرسانی status/ping بدون قفل کامل سیکل)
    now = datetime.now(timezone.utc)
    lines = [f"⚡ <b>نتیجه تست پروکسی‌های bot_{order_id}:</b>\n"]
    healthy = 0
    async with async_session() as session:
        for pd, res in zip(data, results):
            ok, latency = (False, None) if isinstance(res, Exception) else res
            if ok:
                healthy += 1
                new = "weak" if (latency or 0) > config.PROXY_WEAK_MS else "active"
            else:
                new = "dead"
            await session.execute(
                update(FactoryProxy).where(FactoryProxy.id == pd["id"])
                .values(status=new, ping_ms=latency, last_checked_at=now)
            )
            icon = {"active": "🟢", "weak": "🟡", "dead": "🔴"}[new]
            lines.append(f"{icon} <code>{mask_proxy(pd['proxy_string'])}</code>"
                         f" — {latency if latency is not None else '—'}ms")
        await session.commit()

    lines.append(f"\n🟢 پاسخ‌گو: {healthy} از {len(data)}")
    return {"ok": True, "msg": "\n".join(lines), "healthy": healthy, "total": len(data)}


# ============================================================
# پاکسازی موقع حذف اینستنس
# ============================================================
async def cleanup_instance(order_id: int) -> None:
    """حذف تخصیص‌های یک اینستنس که کاملاً destroy شده است."""
    try:
        async with async_session() as session:
            await session.execute(
                delete(InstanceProxy).where(InstanceProxy.order_id == int(order_id)))
            await session.commit()
        logger.info("🧹 تخصیص‌های پروکسی bot_%s پاک شد", order_id)
    except Exception:
        logger.exception("پاکسازی تخصیص‌های bot_%s ناموفق", order_id)


# ============================================================
# نمای آماری برای پنل اینستنس‌ها
# ============================================================
async def brief_for_orders() -> dict:
    """خلاصه سبک برای نمایش کنار هر اینستنس در لیست‌ها: {order_id: 'L8/S5'}"""
    s = await summary_for_orders()
    return {oid: f"L{v['login']}/S{v['sender']}" for oid, v in s.items()}
