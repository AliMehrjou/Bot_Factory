"""
پنل مدیریت استخر مرکزی پروکسی — رابط ادمین
===============================================
/proxies → منوی استخر مرکزی:
  ➕ افزودن (چند خطی) | 🗂 پروکسی اینستنس‌ها | ♻️ تست همه | 📋 لیست استخر
  🧹 حذف مُردها | 📄 خروجی متنی | 🔄 سینک دستی
فرمت‌های قابل قبول:
  socks5://user:pass@host:port
  host:port:user:pass
  host:port

🎯 برای مدیریت «تفکیک‌شده هر اینستنس» (لاگین/سندر اختصاصی) از دکمه
  «🗂 پروکسی اینستنس‌ها» یا لیست اینستنس‌ها (/panel → اینستنس‌ها) استفاده کنید.
"""
import logging
import datetime as dt

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from bot.keyboards import (proxy_menu_kb, proxy_list_manage_kb, confirm_proxy_del_kb,
                           confirm_purge_kb, admin_panel_kb, proxy_detail_kb)
from config import config
from core import proxy_manager
from database import async_session
from database.models import FactoryProxy, InstanceProxy, Order
from sqlalchemy import update, select

logger = logging.getLogger(__name__)
router = Router(name="proxy_panel")


class AddProxyState(StatesGroup):
    waiting_list = State()


class EditProxyState(StatesGroup):
    waiting_new = State()


# ============================================================
# ورودی اصلی
# ============================================================
async def _menu_text() -> str:
    st = await proxy_manager.stats()
    sync = st["sync"]
    asg = st.get("assigned", {})
    return (
        "🌐 <b>مدیریت پروکسی‌های ربات‌ساز</b>\n\n"
        f"🟢 سالم: <b>{st['active']}</b>  |  🟡 کند: <b>{st['weak']}</b>  |  "
        f"🔴 مُرد: <b>{st['dead']}</b>  |  ⚪ غیرفعال: {st['disabled']}\n"
        f"⏱ میانگین پینگ: {st['avg_ping'] or '—'} ms\n"
        f"🔁 آخرین تست: {st['last_cycle']} (سیکل {st['cycle']}) — هر {config.PROXY_CHECK_INTERVAL}s\n\n"
        f"🎯 <b>تخصیص‌های اینستنس‌ها:</b> {asg.get('enabled', 0)} تخصیص روشن "
        f"در {asg.get('instances', 0)} اینستنس از {st['instances']} اینستنس فعال\n\n"
        "🔄 <b>تاریخچه سینک به اینستنس‌ها:</b>\n"
        f"✅ {sync.get('ok', 0)} موفق | ❌ {sync.get('fail', 0)} ناموفق"
        f" | آخرین: {sync.get('last', '—')} | {sync.get('proxies', 0)} پروکسی\n"
        f"📋 <b>آخرین وضعیت اینستنس‌ها:</b>\n{sync.get('details', '')}\n\n"
        "💡 استخر مرکزی = انبار واحد پروکسی (تست مداوم). برای پروکسی‌های "
        "«اختصاصی هر اینستنس» (لاگین/سندر) از «🗂 پروکسی اینستنس‌ها» وارد شوید.\n"
        "اینستنس‌های بدون تخصیص اختصاصی، در حالت legacy از استخر عمومی می‌گیرند."
    )


@router.message(Command("proxies"))
async def cmd_proxies(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(await _menu_text(), reply_markup=proxy_menu_kb())


@router.callback_query(F.data == "px:menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await cb.message.edit_text(await _menu_text(), reply_markup=proxy_menu_kb())


# ============================================================
# افزودن پروکسی (چند خطی)
# ============================================================
@router.callback_query(F.data == "px:add")
async def cb_add(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer(
        "➕ <b>لیست پروکسی‌ها را بفرستید</b> (هر خط یک پروکسی):\n\n"
        "<code>socks5://user:pass@host:port</code>\n"
        "<code>host:port:user:pass</code>\n"
        "<code>host:port</code>\n\n"
        "می‌توانید ده‌ها خط را یکجا Paste کنید. برای لغو /cancel را بزنید."
    )
    await state.set_state(AddProxyState.waiting_list)


@router.message(AddProxyState.waiting_list, F.text)
async def st_add_list(message: Message, state: FSMContext, bot: Bot):
    lines = [line for line in message.text.split("\n") if line.strip()]
    if len(lines) > 300:
        await message.answer("❌ تعداد خطوط ارسال‌شده بیش از حد مجاز است (حداکثر ۳۰۰ خط پروکسی در هر بار). لطفاً پروکسی‌ها را در بخش‌های کوچکتر ارسال کنید.")
        return

    result = await proxy_manager.add_proxies_from_text(message.text)
    await state.clear()

    lines = [f"📥 <b>نتیجه افزودن:</b>\n{result['msg']}"]
    if result.get("invalid_lines"):
        lines.append("\n⚠️ <b>خط‌های نامعتبر:</b>")
        lines += [f"<code>{l[:80]}</code>" for l in result["invalid_lines"]]
        lines.append("فرمت صحیح: <code>socks5://user:pass@host:port</code> یا <code>host:port:user:pass</code>")

    # تست فوری همان‌جا
    if result["added"] > 0:
        lines.append("\n🔍 در حال تست پروکسی‌های جدید...")
        await message.answer("\n".join(lines))
        try:
            await proxy_manager.check_cycle(force=True)
            await message.answer(await proxy_manager.top_list(15),
                                 reply_markup=proxy_menu_kb())
        except Exception as e:
            logger.exception("تست فوری ناموفق")
            await message.answer(f"تست فوری با خطا مواجه شد (در سیکل بعدی دوباره تست می‌شوند): {e}",
                                 reply_markup=proxy_menu_kb())
        return
    await message.answer("\n".join(lines), reply_markup=proxy_menu_kb())

@router.message(AddProxyState.waiting_list)
async def st_add_list_invalid(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ ورودی نامعتبر. لطفاً فقط متن حاوی لیست پروکسی‌ها را ارسال کنید. عملیات لغو شد.", reply_markup=proxy_menu_kb())


# ============================================================
# تست همه
# ============================================================
@router.callback_query(F.data == "px:check")
async def cb_check(cb: CallbackQuery):
    await cb.answer("🔍 تست شروع شد...")
    try:
        result = await proxy_manager.check_cycle(force=True)
        tr = len(result.get("transitions", []))
        await cb.message.answer(
            f"✅ تست {result.get('checked', 0)} پروکسی انجام شد.\n"
            f" تغییر وضعیت: {tr} | سینک به {result.get('synced', 0)} اینستنس\n\n"
            + await proxy_manager.top_list(15),
            reply_markup=proxy_menu_kb(),
        )
    except Exception as e:
        await cb.message.answer(f"🔴 خطا در تست: {e}", reply_markup=proxy_menu_kb())


# ============================================================
# لیست بهترین‌ها و مدیریت
# ============================================================
@router.callback_query(F.data == "px:list")
async def cb_list(cb: CallbackQuery):
    await cb.answer()
    text, proxies = await proxy_manager.top_list_with_objects(20)
    # شمارش اینستنس‌های استفاده‌کننده از هر پروکسی
    async with async_session() as session:
        rows = (await session.execute(
            select(InstanceProxy.proxy_id)
            .where(InstanceProxy.enabled == True)
        )).all()
    usage_map = {}
    for (proxy_id,) in rows:
        usage_map[int(proxy_id)] = usage_map.get(int(proxy_id), 0) + 1
    try:
        await cb.message.edit_text(text, reply_markup=proxy_list_manage_kb(proxies, usage_map))
    except Exception:
        await cb.message.answer(text, reply_markup=proxy_list_manage_kb(proxies, usage_map))


# ============================================================
# حذف مُردها
# ============================================================
@router.callback_query(F.data == "px:purge")
async def cb_purge(cb: CallbackQuery):
    await cb.answer()
    await cb.message.edit_text(
        "🧹 همه پروکسی‌های <b>مرده</b> از استخر مرکزی حذف شوند؟\n"
        "(از اینستنس‌ها فقط غیرفعال شده بودند و چرخه خودشان آنها را مدیریت می‌کند)",
        reply_markup=confirm_purge_kb(),
    )


@router.callback_query(F.data == "px:purge:yes")
async def cb_purge_yes(cb: CallbackQuery):
    await cb.answer()
    n = await proxy_manager.purge_dead()
    await cb.message.edit_text(
        f"🗑 {n} پروکسی مرده حذف شد.\n\n" + await _menu_text(),
        reply_markup=proxy_menu_kb(),
    )


# ============================================================
# خروجی متنی
# ============================================================
@router.callback_query(F.data == "px:export")
async def cb_export(cb: CallbackQuery):
    from aiogram.types import BufferedInputFile
    await cb.answer()
    text = await proxy_manager.export_text()
    await cb.message.answer_document(
        BufferedInputFile(text.encode("utf-8"), filename="proxies_backup.txt"),
        caption="🌐 بکاپ کامل پروکسی‌ها",
    )

# ============================================================
# سینک دستی
# ============================================================
@router.callback_query(F.data == "px:sync")
async def cb_sync(cb: CallbackQuery):
    await cb.answer("🔄 سینک شروع شد...")
    n = await proxy_manager.sync_all_instances("دستی")
    await cb.message.answer(
        f"🔄 سینک به {n} اینستنس انجام شد.\n\n" + await _menu_text(),
        reply_markup=proxy_menu_kb(),
    )


# ============================================================
# مدیریت تک پروکسی (حذف و غیرفعال‌‌سازی)
# ============================================================
@router.callback_query(F.data.startswith("px:delconf:"))
async def cb_del_conf(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    await cb.message.edit_text(
        f"آیا از حذف دائم پروکسی #{pid} اطمینان دارید؟", 
        reply_markup=confirm_proxy_del_kb(pid)
    )


@router.callback_query(F.data.startswith("px:del:"))
async def cb_del(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    ok = await proxy_manager.remove_proxy(pid)
    await cb.answer("🗑 حذف شد" if ok else "یافت نشد", show_alert=not ok)
    if ok:
        text, proxies = await proxy_manager.top_list_with_objects(20)
        await cb.message.edit_text(text, reply_markup=proxy_list_manage_kb(proxies))


@router.callback_query(F.data.startswith("px:disable:"))
async def cb_disable(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    await proxy_manager.disable_proxy(pid)
    await cb.answer("⚪ غیرفعال شد")
    text, proxies = await proxy_manager.top_list_with_objects(20)
    await cb.message.edit_text(text, reply_markup=proxy_list_manage_kb(proxies))


@router.callback_query(F.data.startswith("px:enable:"))
async def cb_enable(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    async with async_session() as session:
        await session.execute(update(FactoryProxy).where(FactoryProxy.id == pid).values(status="active"))
        await session.commit()
    await cb.answer("🟢 فعال شد")
    text, proxies = await proxy_manager.top_list_with_objects(20)
    await cb.message.edit_text(text, reply_markup=proxy_list_manage_kb(proxies))


# ============================================================
# 🎯 نمای جزئیات یک پروکسی استخر مرکزی (کلیک روی ردیف)
# ============================================================
@router.callback_query(F.data.startswith("px:view:"))
async def cb_view(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    await cb.answer()
    async with async_session() as session:
        p = await session.get(FactoryProxy, pid)
        if not p:
            return await cb.message.edit_text("❌ پروکسی یافت نشد.", reply_markup=proxy_menu_kb())
        # اینستنس‌هایی که این پروکسی را تخصیص دارند
        rows = (await session.execute(
            select(InstanceProxy, Order)
            .join(Order, Order.id == InstanceProxy.order_id)
            .where(InstanceProxy.proxy_id == pid)
            .order_by(InstanceProxy.order_id)
        )).all()

    usage_lines = []
    for ap, o in rows[:15]:
        t = {"login": "🔑", "sender": "📤", "both": "♻️"}.get(ap.usage_type, "؟")
        usage_lines.append(f"{t} bot_{o.id} (@{o.bot_username or '—'}){'' if ap.enabled else ' ⏸'}")
    if len(rows) > 15:
        usage_lines.append(f"… و {len(rows) - 15} اینستنس دیگر")
    usage_text = "\n".join(usage_lines) if usage_lines else "— (به هیچ اینستنسی تخصیص نیست)"

    icons = {"active": "🟢 سالم", "weak": "🟡 کند", "dead": "🔴 مرده", "disabled": "⚪ غیرفعال"}
    text = (
        f"🔍 <b>جزئیات پروکسی #{p.id}</b> (استخر مرکزی)\n\n"
        f"🌐 آدرس: <code>{p.scheme}://{p.proxy_string}</code>\n"
        f"📊 وضعیت: <b>{icons.get(p.status, p.status)}</b> | "
        f"پینگ: {p.ping_ms if p.ping_ms is not None else '—'}ms\n"
        f"🕒 آخرین تست: {p.last_checked_at.strftime('%m-%d %H:%M') if p.last_checked_at else '—'} | "
        f"آخرین سینک: {p.last_synced_at.strftime('%m-%d %H:%M') if p.last_synced_at else '—'}\n\n"
        f"📦 <b>تخصیص به {len(rows)} اینستنس:</b>\n{usage_text}\n\n"
        "⚠️ ویرایش/حذف در استخر مرکزی روی همه اینستنس‌های بالا اثر دارد."
    )
    try:
        await cb.message.edit_text(text, reply_markup=proxy_detail_kb(pid, p.status == "disabled"))
    except Exception:
        await cb.message.answer(text, reply_markup=proxy_detail_kb(pid, p.status == "disabled"))


# ============================================================
# ⚡ تست تک پروکسی استخر مرکزی
# ============================================================
@router.callback_query(F.data.startswith("px:tst:"))
async def cb_test_one(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    await cb.answer("⚡ تست در جریان…")
    async with async_session() as session:
        p = await session.get(FactoryProxy, pid)
    if not p:
        return await cb.message.answer("❌ پروکسی یافت نشد.")
    ok, latency = await proxy_manager._check_one(p)
    new_status = ("weak" if (latency or 0) > config.PROXY_WEAK_MS else "active") if ok else "dead"
    async with async_session() as session:
        await session.execute(
            update(FactoryProxy).where(FactoryProxy.id == pid)
            .values(status=new_status, ping_ms=latency,
                    last_checked_at=dt.datetime.now(dt.timezone.utc)))
        await session.commit()
    icon = {"active": "🟢", "weak": "🟡", "dead": "🔴"}[new_status]
    await cb.message.answer(
        f"⚡ <b>نتیجه تست پروکسی #{pid}</b>\n"
        f"{icon} <code>{p.scheme}://{p.proxy_string}</code>\n"
        f"{'✅ موفق — پینگ: ' + str(latency) + 'ms' if ok else '❌ ناموفق (اتصال برقرار نشد)'}\n\n"
        f"وضعیت استخر به «{new_status}» به‌روز شد.",
        reply_markup=proxy_detail_kb(pid, False),
    )


# ============================================================
# ✏️ ویرایش پروکسی استخر مرکزی (FSM — همه اینستنس‌ها)
# ============================================================
@router.callback_query(F.data.startswith("px:editask:"))
async def cb_edit_ask(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":")[2])
    async with async_session() as session:
        p = await session.get(FactoryProxy, pid)
    if not p:
        await cb.answer("❌ پروکسی یافت نشد", show_alert=True)
        return
    await cb.answer()
    await state.set_state(EditProxyState.waiting_new)
    await state.update_data(pid=pid)
    await cb.message.answer(
        f"✏️ <b>ویرایش پروکسی #{pid} در استخر مرکزی</b>\n\n"
        f"مقدار فعلی: <code>{p.scheme}://{p.proxy_string}</code>\n\n"
        "رشته جدید را بفرستید (یک خط):\n"
        "<code>socks5://user:pass@host:port</code> یا <code>host:port:user:pass</code>\n\n"
        "⚠️ <b>هشدار:</b> این تغییر روی «همه اینستنس‌هایی که این پروکسی را دارند» "
        "اعمال می‌شود (رشته قدیمی از همه خارج، جدید جایگزین می‌شود).\n"
        "برای ویرایش فقط یک اینستنس، از پنل آن اینستنس ویرایش کنید.\n\n"
        "برای لغو /cancel بزنید."
    )


@router.message(EditProxyState.waiting_new, F.text)
async def st_edit_apply(message: Message, state: FSMContext):
    data = await state.get_data()
    pid = data.get("pid")
    await state.clear()
    if pid is None:
        return await message.answer("❌ نشست منقضی شده؛ دوباره تلاش کنید.")
    result = await proxy_manager.edit_central_proxy(pid, message.text.strip())
    await message.answer(result["msg"], reply_markup=proxy_menu_kb())


@router.message(EditProxyState.waiting_new)
async def st_edit_invalid(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ فقط متن (رشته پروکسی) قابل قبول است. عملیات لغو شد.")