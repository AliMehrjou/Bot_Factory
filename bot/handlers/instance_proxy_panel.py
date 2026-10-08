"""
🎯 پنل مدیریت پروکسی «هر اینستنس» — رابط ادمین
=================================================
خواسته کارفرما: «خودم از طریق ربات‌ساز برای هر اینستنسی که باشه پروکسی لاگین و
سندر اضافه کنم، حذف کنم، ادیت بزنم، تست بگیرم — کاربر اصلاً دخلی نداشته باشه.»

مسیرها:
  /panel → مدیریت پروکسی‌ها → 🗂 پروکسی اینستنس‌ها → انتخاب اینستنس → پنل اختصاصی
  /instances (لیست اینستنس‌ها) → دکمه 🌐 پروکسی هر ردیف → همان پنل اختصاصی

امکانات پنل هر اینستنس:
  ➕ افزودن چند خطی (لاگین/سندر/هردو)     🎯 تخصیص از استخر مرکزی (بهترین‌ها)
  📋 لیست + جزئیات هر تخصیص             ⚡ تست تک/همه (زنده، از طریق خود پروکسی)
  ✏️ ویرایش آدرس/اعتبارنامه              🔀 تغییر نوع استفاده
  ⏸/▶️ غیرفعال/فعال (بدون حذف)           🗑 حذف تک/گروهی/مرده‌ها
  📄 خروجی متنی                          🔄 سینک فوری همین اینستنس
"""
import logging

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from sqlalchemy import select, update

from bot.keyboards import (
    instance_proxy_picker_kb, instance_proxy_menu_kb, instance_proxy_list_kb,
    instance_proxy_detail_kb, instance_proxy_type_kb, type_pick_kb,
    confirm_instance_rm_kb, confirm_instance_rmall_kb,
)
from config import config
from core import instance_proxies as ipx
from database import async_session
from database.models import Order, FactoryProxy

logger = logging.getLogger(__name__)
router = Router(name="instance_proxy_panel")


class IPXStates(StatesGroup):
    waiting_add_list = State()    # data: oid, ptype
    waiting_pool_count = State()  # data: oid, ptype
    waiting_edit = State()        # data: oid, apid


# ============================================================
# ابزار
# ============================================================
async def _get_order(order_id: int) -> Order | None:
    async with async_session() as session:
        return await session.get(Order, int(order_id))


async def _safe_edit(cb: CallbackQuery, text: str, kb=None):
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)


async def _menu_text(order: Order) -> str:
    st = await ipx.instance_stats(order.id)
    L, S, B = st["login"], st["sender"], st["both"]
    mode_line = (
        "🎯 <b>حالت: تخصیص اختصاصی</b> (فقط پروکسی‌های زیر به این ربات سینک می‌شود)"
        if st["mode"] == "per-instance" else
        "♻️ <b>حالت: عمومی (legacy)</b> — هنوز تخصیص اختصاصی ثبت نشده؛ اینستنس از چرخش "
        "عمومی استخر سالم استفاده می‌کند. با «➕ افزودن» یا «🎯 تخصیص از استخر» این حالت "
        "به اختصاصی تغییر می‌کند."
    )
    warn = ""
    healthy_login = L["healthy"] + B["healthy"]
    healthy_sender = S["healthy"] + B["healthy"]
    if st["mode"] == "per-instance" and (
            healthy_login < config.INSTANCE_PROXY_MIN_HEALTHY
            or healthy_sender < config.INSTANCE_PROXY_MIN_HEALTHY):
        warn = (f"\n\n⚠️ <b>هشدار کمبود:</b> سهمیه سالم کم است "
                f"(لاگین: {healthy_login} | سندر: {healthy_sender} — "
                f"حداقل پیشنهادی هرکدام: {config.INSTANCE_PROXY_MIN_HEALTHY})")
    return (
        f"🎯 <b>پروکسی‌های bot_{order.id}</b> — @{order.bot_username or '—'}\n"
        f"👤 مشتری: {order.tg_name or order.tg_id}\n\n"
        f"{mode_line}\n\n"
        f"🔑 <b>لاگین:</b> {L['total']} تخصیص "
        f"(🟢 {L['healthy']} | 🟡 {L['weak']} | 🔴 {L['dead']} | ⏸ {L['total'] - L['enabled']})\n"
        f"📤 <b>سندر:</b> {S['total']} تخصیص "
        f"(🟢 {S['healthy']} | 🟡 {S['weak']} | 🔴 {S['dead']} | ⏸ {S['total'] - S['enabled']})\n"
        f"♻️ <b>هردو:</b> {B['total']} تخصیص "
        f"(🟢 {B['healthy']} | 🟡 {B['weak']} | 🔴 {B['dead']} | ⏸ {B['total'] - B['enabled']})\n\n"
        f"📦 کل: {st['total']} | روشن: {st['enabled']} | 🔴 مرده: {st['dead_assignments']}"
        f"{warn}\n\n"
        "💡 پروکسی‌های تخصیص‌یافته به‌صورت خودکار در جدول proxies دیتابیس همین ربات "
        "می‌نشینند (با همین نوع استفاده) و مشتری نیازی به هیچ کاری ندارد."
    )


# ============================================================
# انتخاب اینستنس
# ============================================================
async def _pick_view(cb: CallbackQuery, page: int = 1):
    per_page = 6
    async with async_session() as session:
        orders = (await session.scalars(
            select(Order).where(Order.status.in_(("deployed", "stopped")))
            .order_by(Order.id.desc())
        )).all()
    if not orders:
        return await _safe_edit(cb, "هیچ اینستنس فعالی وجود ندارد.", None)

    brief = await ipx.brief_for_orders()
    total_pages = (len(orders) + per_page - 1) // per_page
    page = max(1, min(page, total_pages))
    page_orders = orders[(page - 1) * per_page: page * per_page]

    text = (f"🗂 <b>انتخاب اینستنس برای مدیریت پروکسی</b> (صفحه {page}/{total_pages})\n\n"
            "⚠️ اینستنس‌های بدون تخصیص اختصاصی هنوز در حالت legacy از استخر عمومی می‌گیرند.\n"
            "L = پروکسی لاگین | S = پروکسی سندر")
    await _safe_edit(cb, text, instance_proxy_picker_kb(page_orders, page, total_pages, brief))


@router.callback_query(F.data == "ipx:pick")
async def cb_pick(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await _pick_view(cb, 1)


@router.callback_query(F.data.startswith("ipx:pick:"))
async def cb_pick_page(cb: CallbackQuery):
    page = int(cb.data.split(":")[2])
    await cb.answer()
    await _pick_view(cb, page)


# ============================================================
# منوی اصلی اینستنس
# ============================================================
@router.callback_query(F.data.startswith("ipx:menu:"))
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    oid = int(cb.data.split(":")[2])
    order = await _get_order(oid)
    await cb.answer()
    if not order:
        return await _safe_edit(cb, "❌ اینستنس یافت نشد.", None)
    await _safe_edit(cb, await _menu_text(order), instance_proxy_menu_kb(oid))


# ============================================================
# لیست تخصیص‌ها
# ============================================================
@router.callback_query(F.data.startswith("ipx:list:"))
async def cb_list(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid, page = int(parts[2]), int(parts[3]) if len(parts) > 3 else 1
    await cb.answer()
    data = await ipx.instance_list(oid, page)
    if not data["rows"]:
        return await _safe_edit(
            cb, f"📋 bot_{oid} هیچ پروکسی تخصیص‌یافته‌ای ندارد.\n"
                "از «➕ افزودن» یا «🎯 تخصیص از استخر» شروع کنید.",
            instance_proxy_menu_kb(oid))
    from utils.proxy_parse import mask_proxy
    icons = {"active": "🟢", "weak": "🟡", "dead": "🔴", "disabled": "⚪"}
    ticons = {"login": "🔑", "sender": "📤", "both": "♻️"}
    lines = [f"📋 <b>پروکسی‌های bot_{oid}</b> — صفحه {data['page']}/{data['total_pages']}"
             f" (کل {data['total']})\n"]
    for ap, fp in data["rows"]:
        lines.append(
            f"{icons.get(fp.status, '⚪')}{ticons.get(ap.usage_type, '🔑')} "
            f"<code>{mask_proxy(fp.proxy_string)}</code>"
            f"  ⏱ {fp.ping_ms if fp.ping_ms is not None else '—'}ms"
            f"  (#{ap.id}){'' if ap.enabled else ' ⏸غیرفعال'}"
        )
    await _safe_edit(cb, "\n".join(lines),
                     instance_proxy_list_kb(oid, data["rows"], data["page"], data["total_pages"]))


# ============================================================
# جزئیات یک تخصیص + عملیات
# ============================================================
@router.callback_query(F.data.startswith("ipx:view:"))
async def cb_view(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid, apid = int(parts[2]), int(parts[3])
    await cb.answer()
    info = await ipx.get_assignment(apid)
    if not info or info["ap"].order_id != oid:
        return await _safe_edit(cb, "❌ تخصیص یافت نشد.", instance_proxy_menu_kb(oid))
    ap, fp, inst = info["ap"], info["fp"], info["inst_info"]
    t = {"login": "🔑 فقط لاگین", "sender": "📤 فقط سندر", "both": "♻️ هردو"}.get(ap.usage_type, ap.usage_type)

    inst_lines = "— (هنوز سینک نشده یا جدول ساخته نشده)"
    if inst:
        inst_lines = (
            f"فعال: {'✅ بله' if inst['is_active'] else '❌ خیر'} | "
            f"نوع: {inst['usage_type']} | سلامت: {inst['health_state']}\n"
            f"پینگ: {inst['ping_ms'] if inst['ping_ms'] is not None else '—'}ms | "
            f"اکانت‌های روی آن: {inst['in_use']} | شکست‌ها: {inst['fail_count']}"
        )

    text = (
        f"🔍 <b>جزئیات تخصیص #{ap.id}</b> — bot_{oid}\n\n"
        f"🌐 آدرس: <code>{fp.scheme}://{fp.proxy_string}</code>\n"
        f"🎯 نوع استفاده: <b>{t}</b> | وضعیت تخصیص: {'🟢 روشن' if ap.enabled else '⏸ خاموش'}\n\n"
        f"📊 <b>استخر مرکزی:</b>\n"
        f"وضعیت: {fp.status} | پینگ: {fp.ping_ms if fp.ping_ms is not None else '—'}ms | "
        f"آخرین تست: {fp.last_checked_at.strftime('%m-%d %H:%M') if fp.last_checked_at else '—'}\n\n"
        f"📦 <b>وضعیت در دیتابیس این ربات (واقعی):</b>\n{inst_lines}\n\n"
        "یکی از عملیات را انتخاب کنید:"
    )
    await _safe_edit(cb, text, instance_proxy_detail_kb(oid, apid, ap.enabled))


# ============================================================
# تست تک پروکسی (زنده)
# ============================================================
@router.callback_query(F.data.startswith("ipx:tst:"))
async def cb_test_one(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid, apid = int(parts[2]), int(parts[3])
    await cb.answer("⚡ تست در جریان…")
    info = await ipx.get_assignment(apid)
    if not info or info["ap"].order_id != oid:
        return await _safe_edit(cb, "❌ تخصیص یافت نشد.", instance_proxy_menu_kb(oid))
    fp = info["fp"]
    from core.proxy_manager import _check_one
    import datetime as dt
    ok, latency = await _check_one(fp)
    new_status = ("weak" if (latency or 0) > config.PROXY_WEAK_MS else "active") if ok else "dead"
    async with async_session() as session:
        await session.execute(
            update(FactoryProxy).where(FactoryProxy.id == fp.id)
            .values(status=new_status, ping_ms=latency,
                    last_checked_at=dt.datetime.now(dt.timezone.utc)))
        await session.commit()
    icon = {"active": "🟢", "weak": "🟡", "dead": "🔴"}[new_status]
    await cb.message.answer(
        f"⚡ <b>نتیجه تست پروکسی #{apid}</b> (bot_{oid})\n"
        f"{icon} <code>{fp.scheme}://{fp.proxy_string}</code>\n"
        f"{'✅ موفق — پینگ: ' + str(latency) + 'ms' if ok else '❌ ناموفق (اتصال برقرار نشد)'}\n\n"
        f"وضعیت استخر به «{new_status}» به‌روز شد.",
        reply_markup=instance_proxy_detail_kb(oid, apid, info["ap"].enabled),
    )


# ============================================================
# ✏️ ویرایش پروکسی (FSM)
# ============================================================
@router.callback_query(F.data.startswith("ipx:editask:"))
async def cb_edit_ask(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    oid, apid = int(parts[2]), int(parts[3])
    info = await ipx.get_assignment(apid)
    if not info or info["ap"].order_id != oid:
        await cb.answer("❌ تخصیص یافت نشد", show_alert=True)
        return
    await cb.answer()
    await state.set_state(IPXStates.waiting_edit)
    await state.update_data(oid=oid, apid=apid)
    await cb.message.answer(
        f"✏️ <b>ویرایش پروکسی #{apid} در bot_{oid}</b>\n\n"
        f"مقدار فعلی: <code>{info['fp'].scheme}://{info['fp'].proxy_string}</code>\n\n"
        "رشته جدید را بفرستید (یک خط):\n"
        "<code>socks5://user:pass@host:port</code>\n"
        "<code>host:port:user:pass</code>\n\n"
        "فقط همین اینستنس به پروکسی جدید منتقل می‌شود؛ سایر اینستنس‌ها دست نمی‌خورند. "
        "برای لغو /cancel بزنید."
    )


@router.message(IPXStates.waiting_edit, F.text)
async def st_edit_apply(message: Message, state: FSMContext):
    data = await state.get_data()
    oid, apid = data.get("oid"), data.get("apid")
    await state.clear()
    if oid is None:
        return await message.answer("❌ نشست منقضی شده؛ دوباره تلاش کنید.")
    result = await ipx.edit_assignment_proxy(apid, message.text.strip())
    kb = instance_proxy_menu_kb(oid)
    await message.answer(result["msg"], reply_markup=kb)
    if result.get("invalid_lines"):
        await message.answer("فرمت صحیح: <code>socks5://user:pass@host:port</code>")


@router.message(IPXStates.waiting_edit)
async def st_edit_invalid(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ فقط متن (رشته پروکسی) قابل قبول است. عملیات لغو شد.")


# ============================================================
# 🔀 تغییر نوع استفاده
# ============================================================
@router.callback_query(F.data.startswith("ipx:type:"))
async def cb_type_menu(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid, apid = int(parts[2]), int(parts[3])
    await cb.answer()
    await _safe_edit(
        cb, f"🔀 <b>نوع استفاده تخصیص #{apid}</b> (bot_{oid}) را انتخاب کنید:\n\n"
            "🔑 لاگین → فقط برای لاگین اکانت‌ها\n"
            "📤 سندر → فقط برای ارسال (تخصیص به اکانت‌ها)\n"
            "♻️ هردو → هر دو مسیر",
        instance_proxy_type_kb(oid, apid))


@router.callback_query(F.data.startswith("ipx:settype:"))
async def cb_set_type(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid, apid, new_type = int(parts[2]), int(parts[3]), parts[4]
    result = await ipx.set_usage_type(apid, new_type)
    await cb.answer("✅ انجام شد" if result["ok"] else "❌ خطا", show_alert=not result["ok"])
    if result["ok"]:
        info = await ipx.get_assignment(apid)
        await _safe_edit(cb, result["msg"], instance_proxy_detail_kb(oid, apid, info["ap"].enabled))


# ============================================================
# ⏸/▶️ فعال/غیرفعال
# ============================================================
@router.callback_query(F.data.startswith("ipx:off:"))
async def cb_off(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid, apid = int(parts[2]), int(parts[3])
    result = await ipx.set_enabled(apid, False)
    await cb.answer("⚪ غیرفعال شد" if result["ok"] else "❌ خطا")
    if result["ok"]:
        await _safe_edit(cb, f"⚪ تخصیص #{apid} غیرفعال شد و از bot_{oid} خارج گردید "
                             "(پروکسی در استخر مرکزی باقی می‌ماند).",
                         instance_proxy_detail_kb(oid, apid, False))


@router.callback_query(F.data.startswith("ipx:on:"))
async def cb_on(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid, apid = int(parts[2]), int(parts[3])
    result = await ipx.set_enabled(apid, True)
    await cb.answer("🟢 فعال شد" if result["ok"] else "❌ خطا")
    if result["ok"]:
        await _safe_edit(cb, f"🟢 تخصیص #{apid} فعال شد و به bot_{oid} بازگشت.",
                         instance_proxy_detail_kb(oid, apid, True))


# ============================================================
# 🗑 حذف تک تخصیص
# ============================================================
@router.callback_query(F.data.startswith("ipx:rmask:"))
async def cb_rmask(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid, apid = int(parts[2]), int(parts[3])
    if len(parts) == 5 and parts[4] == "yes":
        result = await ipx.unassign(apid)
        await cb.answer("🗑 حذف شد" if result["ok"] else "❌ خطا")
        order = await _get_order(oid)
        return await _safe_edit(cb, result["msg"] + "\n\n" + await _menu_text(order),
                                instance_proxy_menu_kb(oid))
    await cb.answer()
    await _safe_edit(
        cb, f"⚠️ حذف تخصیص #{apid} از bot_{oid}؟\n\n"
            "پروکسی از این اینستنس خارج می‌شود (در استخر مرکزی باقی می‌ماند و اگر "
            "اینستنس دیگری آن را داشته باشد دست نمی‌خورد).",
        confirm_instance_rm_kb(oid, apid))


# ============================================================
# 💥 حذف همه / 🧹 حذف مرده‌ها
# ============================================================
@router.callback_query(F.data.startswith("ipx:rmall:"))
async def cb_rmall(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid = int(parts[2])
    if len(parts) == 4 and parts[3] == "yes":
        result = await ipx.unassign_all(oid, only_dead=False)
        await cb.answer()
        order = await _get_order(oid)
        warn = ("\n\n♻️ اینستنس به حالت legacy (چرخش عمومی استخر) برگشت."
                if order else "")
        return await _safe_edit(cb, result["msg"] + warn + "\n\n" + await _menu_text(order),
                                instance_proxy_menu_kb(oid))
    await cb.answer()
    await _safe_edit(
        cb, f"💥 <b>حذف همه تخصیص‌های bot_{oid}؟</b>\n\n"
            "همه پروکسی‌ها از این اینستنس خارج می‌شوند و اینستنس به حالت legacy "
            "(چرخش عمومی استخر سالم) برمی‌گردد. این عمل قابل بازگشت نیست (تخصیص‌ها "
            "باید دوباره ساخته شوند).",
        confirm_instance_rmall_kb(oid, only_dead=False))


@router.callback_query(F.data.startswith("ipx:rmdead:"))
async def cb_rmdead(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid = int(parts[2])
    if len(parts) == 4 and parts[3] == "yes":
        result = await ipx.unassign_all(oid, only_dead=True)
        await cb.answer()
        order = await _get_order(oid)
        return await _safe_edit(cb, result["msg"] + "\n\n" + await _menu_text(order),
                                instance_proxy_menu_kb(oid))
    await cb.answer()
    await _safe_edit(
        cb, f"🧹 حذف تخصیص‌های «مرده» از bot_{oid}؟\n\n"
            "فقط تخصیص‌هایی که پروکسی‌شان DEAD شده حذف می‌شوند تا لیست تمیز بماند.",
        confirm_instance_rmall_kb(oid, only_dead=True))


# ============================================================
# ➕ افزودن پروکسی جدید (انتخاب نوع → لیست چندخطی)
# ============================================================
@router.callback_query(F.data.startswith("ipx:add:"))
async def cb_add(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    oid = int(parts[2])
    await cb.answer()

    if len(parts) == 3:  # انتخاب نوع
        return await _safe_edit(
            cb, f"➕ <b>افزودن پروکسی به bot_{oid}</b>\n\n"
                "نوع استفاده را انتخاب کنید:",
            type_pick_kb(oid, "add"))

    ptype = parts[3]
    await state.set_state(IPXStates.waiting_add_list)
    await state.update_data(oid=oid, ptype=ptype)
    t = {"login": "🔑 لاگین", "sender": "📤 سندر", "both": "♻️ هردو"}[ptype]
    await cb.message.answer(
        f"➕ <b>لیست پروکسی‌ها را بفرستید</b> — bot_{oid} ({t})\n"
        "هر خط یک پروکسی (حداکثر ۳۰۰ خط):\n\n"
        "<code>socks5://user:pass@host:port</code>\n"
        "<code>host:port:user:pass</code>\n"
        "<code>host:port</code>\n\n"
        "پروکسی‌ها در استخر مرکزی هم ذخیره می‌شوند و بلافاصله تست و سینک می‌گردند. "
        "برای لغو /cancel را بزنید."
    )


@router.message(IPXStates.waiting_add_list, F.text)
async def st_add_list(message: Message, state: FSMContext):
    data = await state.get_data()
    oid, ptype = data.get("oid"), data.get("ptype")
    await state.clear()
    if oid is None:
        return await message.answer("❌ نشست منقضی شده؛ دوباره تلاش کنید.")

    lines = [l for l in message.text.split("\n") if l.strip()]
    if len(lines) > config.INSTANCE_PROXY_MAX_LINES:
        return await message.answer(
            f"❌ بیش از حد مجاز ({config.INSTANCE_PROXY_MAX_LINES} خط). "
            "در بخش‌های کوچکتر بفرستید.")

    result = await ipx.assign_text_to_instance(oid, message.text, ptype)
    text = result["msg"]
    if result.get("invalid_lines"):
        text += "\n\n⚠️ <b>خط‌های نامعتبر:</b>\n" + "\n".join(
            f"<code>{l[:80]}</code>" for l in result["invalid_lines"])
    order = await _get_order(oid)
    kb = instance_proxy_menu_kb(oid)
    await message.answer(text + "\n\n" + await _menu_text(order), reply_markup=kb)


@router.message(IPXStates.waiting_add_list)
async def st_add_invalid(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ فقط متن حاوی لیست پروکسی‌ها قابل قبول است. عملیات لغو شد.")


# ============================================================
# 🎯 تخصیص از استخر مرکزی (انتخاب نوع → تعداد)
# ============================================================
@router.callback_query(F.data.startswith("ipx:pool:"))
async def cb_pool(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    oid = int(parts[2])
    await cb.answer()

    if len(parts) == 3:
        return await _safe_edit(
            cb, f"🎯 <b>تخصیص از استخر مرکزی به bot_{oid}</b>\n\n"
                "نوع استفاده را انتخاب کنید:",
            type_pick_kb(oid, "pool"))

    ptype = parts[3]
    # آمار استخر برای راهنما
    from core import proxy_manager
    st = await proxy_manager.stats()
    healthy = st["active"] + st["weak"]
    await state.set_state(IPXStates.waiting_pool_count)
    await state.update_data(oid=oid, ptype=ptype)
    t = {"login": "🔑 لاگین", "sender": "📤 سندر", "both": "♻️ هردو"}[ptype]
    await cb.message.answer(
        f"🎯 <b>تخصیص از استخر به bot_{oid}</b> ({t})\n\n"
        f"استخر سالم: 🟢 {st['active']} + 🟡 {st['weak']} = {healthy} پروکسی\n\n"
        "چند پروکسی تخصیص داده شود؟ عدد بفرستید (مثلاً <code>10</code>) "
        "یا کلمه <code>all</code> برای همه سالم‌ها.\n"
        "انتخاب هوشمند: اول کم‌بارترین‌ها (توزیع بار بین اینستنس‌ها) بعد کم‌پینگ‌ترین‌ها.\n\n"
        "برای لغو /cancel را بزنید."
    )


@router.message(IPXStates.waiting_pool_count, F.text)
async def st_pool_count(message: Message, state: FSMContext):
    data = await state.get_data()
    oid, ptype = data.get("oid"), data.get("ptype")
    await state.clear()
    if oid is None:
        return await message.answer("❌ نشست منقضی شده؛ دوباره تلاش کنید.")

    raw = message.text.strip().lower()
    if raw in ("all", "همه", "all"):
        count = 10_000
    elif raw.isdigit() and int(raw) > 0:
        count = min(int(raw), 10_000)
    else:
        await state.set_state(IPXStates.waiting_pool_count)
        await state.update_data(oid=oid, ptype=ptype)
        return await message.answer("❌ فقط عدد مثبت یا کلمه «all». دوباره بفرستید یا /cancel.")

    result = await ipx.assign_from_pool(oid, count, ptype)
    order = await _get_order(oid)
    await message.answer(result["msg"] + "\n\n" + await _menu_text(order),
                         reply_markup=instance_proxy_menu_kb(oid))


@router.message(IPXStates.waiting_pool_count)
async def st_pool_invalid(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ فقط متن (عدد یا all) قابل قبول است. عملیات لغو شد.")


# ============================================================
# ⚡ تست همه پروکسی‌های اینستنس
# ============================================================
@router.callback_query(F.data.startswith("ipx:check:"))
async def cb_check_all(cb: CallbackQuery):
    oid = int(cb.data.split(":")[2])
    await cb.answer("⚡ تست همه در جریان…")
    result = await ipx.test_instance_proxies(oid)
    order = await _get_order(oid)
    kb = instance_proxy_menu_kb(oid)
    if not result["ok"]:
        return await cb.message.answer(result["msg"], reply_markup=kb)
    # پس از تست، سینک اختصاصی تا وضعیت جدید اعمال شود
    from core.proxy_manager import sync_instance_now
    n = await sync_instance_now(oid)
    await cb.message.answer(
        result["msg"] + f"\n\n🔄 سینک اختصاصی انجام شد ({n} پروکسی).",
        reply_markup=kb)


# ============================================================
# 🔄 سینک فوری همین اینستنس
# ============================================================
@router.callback_query(F.data.startswith("ipx:sync:"))
async def cb_sync_now(cb: CallbackQuery):
    oid = int(cb.data.split(":")[2])
    await cb.answer("🔄 سینک در جریان…")
    from core.proxy_manager import sync_instance_now
    n = await sync_instance_now(oid)
    order = await _get_order(oid)
    if n < 0:
        await cb.message.answer(
            "🔴 سینک ناموفق — اینستنس روشن است؟ (لاگ کانتینر را ببینید)",
            reply_markup=instance_proxy_menu_kb(oid))
    else:
        await cb.message.answer(
            f"🔄 سینک اختصاصی bot_{oid} انجام شد: {n} پروکسی فعال نوشته شد.\n\n"
            + await _menu_text(order),
            reply_markup=instance_proxy_menu_kb(oid))


# ============================================================
# 📄 خروجی متنی اینستنس
# ============================================================
@router.callback_query(F.data.startswith("ipx:export:"))
async def cb_export(cb: CallbackQuery):
    oid = int(cb.data.split(":")[2])
    await cb.answer()
    content = await ipx.export_instance(oid)
    await cb.message.answer_document(
        BufferedInputFile(content.encode("utf-8"), filename=f"proxies_bot_{oid}.txt"),
        caption=f"📄 بکاپ پروکسی‌های bot_{oid} (نوع + وضعیت + پینگ)",
    )
