"""
پنل ادمین — تأیید سفارش‌ها، مدیریت اینستنس‌ها، آمار
=================================================
جریان تأیید: ادمین دکمه «تأیید و دیپلوی» را می‌زند → سفارش به صف دیپلوی سریال
می‌رود → پس از ساخت موفق، هم ادمین و هم مشتری پیام نتیجه + لینک ربات را می‌گیرند.
"""
import logging
import datetime as dt
import html

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func, update, text

from bot.keyboards import admin_panel_kb, confirm_destroy_kb
from config import config, fmt_price, parse_to_cents
from core.deploy_queue import DeployJob
from core import orchestrator
from database import async_session
from database.models import Order

logger = logging.getLogger(__name__)
router = Router(name="admin")


# ============================================================
# /panel
# ============================================================
@router.message(Command("panel"))
async def cmd_panel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 <b>پنل مدیریت ربات‌ساز</b>", reply_markup=admin_panel_kb())


@router.callback_query(F.data == "adm:panel")
async def cb_panel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.edit_text("🛠 <b>پنل مدیریت ربات‌ساز</b>", reply_markup=admin_panel_kb())
    except Exception:
        await cb.message.delete()
        await cb.message.answer("🛠 <b>پنل مدیریت ربات‌ساز</b>", reply_markup=admin_panel_kb())
    await cb.answer()


# ============================================================
# آمار کلی
# ============================================================
@router.callback_query(F.data == "adm:stats")
async def cb_stats(cb: CallbackQuery):
    await cb.answer()
    
    async with async_session() as session:
        # گروه‌بندی وضعیت‌ها
        rows = (await session.execute(
            select(Order.status, func.count(Order.id)).group_by(Order.status)
        )).all()
        by = {r[0]: int(r[1]) for r in rows}
        
        # محاسبه درآمد
        now = dt.datetime.now(dt.timezone.utc)
        first_day_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        revenue_total = await session.scalar(
            select(func.sum(Order.price)).where(
                Order.approved_at.isnot(None),
                Order.status.notin_(("rejected", "failed"))
            )
        ) or 0
        
        revenue_month = await session.scalar(
            select(func.sum(Order.price)).where(
                Order.approved_at >= first_day_of_month,
                Order.status.notin_(("rejected", "failed"))
            )
        ) or 0

    from core.proxy_manager import stats as proxy_stats
    px = await proxy_stats()

    # نمایش آمار صف دیپلوی
    from core.deploy_queue import deploy_queue
    dq_qsize = deploy_queue._q.qsize()
    dq_busy = f"{deploy_queue.busy_workers}/{deploy_queue.num_workers}"
    dq_proc = deploy_queue.stats.get("processed", 0)
    dq_fail = deploy_queue.stats.get("failed", 0)

    # ظرفیت زیرساخت (برای اطمینان از آمادگی موج سفارش‌ها)
    active_now = by.get("deployed", 0) + by.get("stopped", 0) + by.get("provisioning", 0)
    redis_cap = config.redis_capacity
    mysql_cap = config.max_instances_by_mysql

    text = (
        "📊 <b>آمار ربات‌ساز</b>\n\n"
        "🛎 در انتظار تأیید: " + str(by.get("awaiting_approval", 0)) + "\n"
        "🟢 فعال: " + str(by.get("deployed", 0)) + "\n"
        "⏸ متوقف: " + str(by.get("stopped", 0)) + "\n"
        "⌛️ منقضی: " + str(by.get("expired", 0)) + "\n"
        "❌ رد‌شده: " + str(by.get("rejected", 0)) + "\n"
        "🔴 خراب: " + str(by.get("failed", 0)) + "\n\n"
        f"🏗 <b>ظرفیت:</b> اینستنس فعال: {active_now} | اسلات Redis: {redis_cap} | سقف MySQL: {mysql_cap}\n\n"
        f"💰 کل درآمد: <b>{fmt_price(revenue_total)} {config.CURRENCY}</b>\n"
        f"💵 درآمد این ماه: <b>{fmt_price(revenue_month)} {config.CURRENCY}</b>\n\n"
        "🌐 <b>پروکسی‌ها:</b>\n"
        f"🟢 سالم: {px['active']} | 🟡 کند: {px['weak']} | 🔴 مُرد: {px['dead']}\n"
        f"⏱ میانگین پینگ: {px['avg_ping'] or '—'} ms | آخرین تست: {px['last_cycle']}\n\n"
        "⚙️ <b>وضعیت صف دیپلوی:</b>\n"
        f"کارگر: {dq_busy} | طول صف: {dq_qsize}\n"
        f"انجام‌شده: {dq_proc} | خطا: {dq_fail}"
    )
    try:
        await cb.message.edit_text(text, reply_markup=admin_panel_kb())
    except Exception:
        await cb.message.answer(text, reply_markup=admin_panel_kb())


@router.callback_query(F.data.startswith("inst:retry:"))
async def cb_inst_retry(cb: CallbackQuery, bot: Bot):
    oid = int(cb.data.split(":")[2])
    
    from core.deploy_queue import DeployJob, deploy_queue
    async def notify(result: dict):
        text = f"نتیجه تلاش مجدد bot_{oid}: " + ("موفق" if result.get('ok') else "ناموفق")
        for admin_id in config.ADMIN_IDS:
            try: await bot.send_message(admin_id, text)
            except: pass

    deploy_queue.submit(DeployJob(action="provision", order_id=oid, done_cb=notify))
    await cb.answer(f"bot_{oid} مجدداً در صف دیپلوی قرار گرفت")


# ============================================================
# تأیید / رد سفارش
# ============================================================
@router.callback_query(F.data.startswith("appr:"))
async def cb_approval(cb: CallbackQuery, bot: Bot):
    _, order_id_s, verdict = cb.data.split(":")
    order_id = int(order_id_s)

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order or order.status != 'awaiting_approval':
            return await cb.answer("این سفارش قبلاً پردازش شده یا وجود ندارد.", show_alert=True)

        # 🆕 آیا مبلغ این سفارش موقع ثبت از کیف پول پرداخت شده؟
        # (اگر بله: دیگر نباید دوباره کسر شود و در صورت رد، کل مبلغ عودت داده می‌شود)
        wallet_paid = (order.receipt_file_id == "wallet_paid")

        if verdict == "no":
            order.status = 'rejected'

            # 🆕 عودت وجه برای سفارش‌های پرداخت‌شده از کیف پول
            # (با ORM — سازگار با MySQL و SQLite)
            refund_text = ""
            if wallet_paid:
                from database.models import User
                u = await session.get(User, order.tg_id)
                if u:
                    u.balance = (u.balance or 0) + order.price
                else:
                    session.add(User(tg_id=order.tg_id, balance=order.price))
                refund_text = (
                    f"\n💰 مبلغ <b>{fmt_price(order.price)} {config.CURRENCY}</b> "
                    "به کیف پول شما بازگردانده شد."
                )

            await session.commit()

            try:
                if cb.message.photo:
                    await cb.message.edit_caption(caption=(cb.message.caption or "") + f"\n\n❌ <b>سفارش #{order_id} رد شد</b>", reply_markup=None)
                else:
                    await cb.message.edit_text(f"❌ سفارش #{order_id} رد شد.", reply_markup=None)
            except Exception: pass

            try:
                await bot.send_message(
                    order.tg_id,
                    f"متأسفانه سفارش #{order_id} شما تأیید نشد.{refund_text}\n"
                    f"برای بررسی بیشتر با پشتیبانی ({getattr(config, 'SUPPORT_USERNAME', 'مدیریت')}) در ارتباط باشید."
                )
            except Exception: pass
            return await cb.answer("رد شد" + (" و وجه عودت داده شد" if wallet_paid else ""))

        # اگر تأیید شد
        now_dt = dt.datetime.now(dt.timezone.utc)
        order.approved_at = now_dt

        # === کسر اتوماتیک از کیف پول ===
        # 🆕 فقط برای سفارش‌هایی که هنوز از کیف پول پرداخت نشده‌اند (رسید قدیمی/دستی)؛
        # سفارش‌های «wallet_paid» موقع ثبت خودشان کسر شده‌اند.
        if not wallet_paid:
            user_balance = await session.scalar(text("SELECT balance FROM users WHERE tg_id = :uid"), {"uid": order.tg_id}) or 0
            if user_balance > 0:
                # کسر موجودی به اندازه کل قیمت یا هرچقدر که در کیف پولش مانده
                deduct_amount = min(user_balance, order.price)
                await session.execute(
                    text("UPDATE users SET balance = balance - :amount WHERE tg_id = :uid"),
                    {"amount": deduct_amount, "uid": order.tg_id}
                )
        # === پایان کسر اتوماتیک ===
        
        # اگر سفارش از نوع تمدید باشد
        if order.renew_of:
            old_order = await session.get(Order, order.renew_of)
            if old_order:
                if not old_order.expires_at or old_order.expires_at < now_dt:
                    new_exp = now_dt + dt.timedelta(days=order.duration_days)
                else:
                    new_exp = old_order.expires_at + dt.timedelta(days=order.duration_days)
                
                old_order.expires_at = new_exp
                
                need_start = False
                if old_order.status in ("expired", "stopped"):
                    old_order.status = "deployed"
                    need_start = True
                
                order.status = 'renewed'  # وضعیت پایانه اختصاصی برای سفارش‌های تمدید
                await session.commit()
                
                try:
                    if cb.message.photo:
                        await cb.message.edit_caption(caption=(cb.message.caption or "") + f"\n\n✅ <b>تمدید ربات bot_{old_order.id} تأیید شد</b>", reply_markup=None)
                    else:
                        await cb.message.edit_text(f"✅ تمدید سفارش #{order_id} برای ربات bot_{old_order.id} انجام شد.", reply_markup=None)
                except Exception: pass
                
                try:
                    await bot.send_message(
                        order.tg_id, 
                        f"✅ <b>تمدید ربات شما تأیید شد!</b>\nربات @{order.bot_username} تا تاریخ {new_exp.strftime('%Y/%m/%d')} تمدید شد."
                    )
                except Exception: pass
                
                if need_start:
                    from core.deploy_queue import deploy_queue as dq
                    dq.submit(DeployJob(action="start", order_id=old_order.id))
                return await cb.answer("تمدید با موفقیت انجام شد ✅")
                
        # ثبت وضعیتِ تأیید برای سفارش‌های جدید
        order.status = 'approved'
        await session.commit()

    try:
        if cb.message.photo:
            await cb.message.edit_caption(caption=(cb.message.caption or "") + f"\n\n🚀 <b>سفارش #{order_id} در صف ساخت قرار گرفت</b>", reply_markup=None)
        else:
            await cb.message.edit_text(f"🚀 سفارش #{order_id} در صف ساخت قرار گرفت.", reply_markup=None)
    except Exception: pass
    
    await cb.answer("در صف ساخت قرار گرفت 🚀")

    async def notify(result: dict):
        if result.get("ok"):
            text = (
                f"✅ <b>سفارش #{order_id} ساخته شد!</b>\n"
                f"🤖 ربات: @{order.bot_username}\n"
                f"🔗 {result.get('url', '')}\n"
                "باید در رباتتان /start بزنید تا پنل مدیریت ظاهر شود."
            )
        else:
            safe_error = html.escape(result.get('error', '')[:600])
            text = f"🔴 <b>سفارش #{order_id} شکست خورد:</b>\n<code>{safe_error}</code>"
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text)
            except Exception:
                pass
        try:
            if result.get("ok"):
                await bot.send_message(
                    order.tg_id,
                    "🎉 <b>ربات شما آماده شد!</b>\n\n"
                    f"🔗 لینک ربات: {result.get('url', f'https://t.me/' + order.bot_username)}\n\n"
                    "۱) وارد ربات شوید و <b>/start</b> بزنید\n"
                    "۲) پنل مدیریت ظاهر می‌شود\n"
                    "۳) اولین کار: ثبت پروکسی از بخش تنظیمات یا مستقیم شروع کار\n\n"
                    "موفق باشید! 🚀",
                )
            else:
                await bot.send_message(
                    order.tg_id,
                    f"⚠️ در ساخت سفارش شما مشکلی پیش آمد؛ پشتیبانی با شما در تماس خواهد بود."
                )
        except Exception:
            pass

    from core.deploy_queue import deploy_queue as dq
    dq.submit(DeployJob(action="provision", order_id=order_id, done_cb=notify))


# ============================================================
# سفارش‌های در انتظار (صفحه‌بندی‌شده - نمایش عکس)
# ============================================================
def admin_pending_page_kb(order_id: int, current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ تأیید", callback_data=f"appr:{order_id}:yes")
    b.button(text="❌ رد", callback_data=f"appr:{order_id}:no")
    
    nav_buttons = 0
    if current_page > 1:
        b.button(text="⬅️ قبلی", callback_data=f"adm:pending:{current_page - 1}")
        nav_buttons += 1
    if current_page < total_pages:
        b.button(text="بعدی ➡️", callback_data=f"adm:pending:{current_page + 1}")
        nav_buttons += 1
        
    sizes = [2]
    if nav_buttons > 0:
        sizes.append(nav_buttons)
        
    b.button(text="↩️ بازگشت به پنل", callback_data="adm:panel")
    sizes.append(1)
    
    b.adjust(*sizes)
    return b.as_markup()

@router.callback_query(F.data.startswith("adm:pending"))
async def cb_pending(cb: CallbackQuery):
    await cb.answer()
    
    parts = cb.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 1
    per_page = 1  # فقط ۱ مورد در هر صفحه تا بتوانیم عکس را تمیز نمایش دهیم
    
    async with async_session() as session:
        orders = (await session.scalars(
            select(Order).where(Order.status == "awaiting_approval")
            .order_by(Order.created_at.asc())
        )).all()
        
    if not orders:
        text = "✅ سفارشی در انتظار تأیید نیست."
        try:
            if cb.message.photo:
                await cb.message.delete()
                await cb.message.answer(text, reply_markup=admin_panel_kb())
            else:
                await cb.message.edit_text(text, reply_markup=admin_panel_kb())
        except Exception:
            pass
        return
        
    total_pages = len(orders)
    page = max(1, min(page, total_pages))
    o = orders[page - 1]
    
    is_renew = f"\n🔄 <b>تمدید برای ربات bot_{o.renew_of}</b>" if o.renew_of else ""
    safe_name = html.escape(o.tg_name or "")
    safe_user = html.escape(o.tg_username or str(o.tg_id))
    text = (
        f"🛎 <b>سفارش #{o.id}</b> {is_renew}\n\n"
        f"👤 مشتری: {safe_name} ({safe_user})\n"
        f"📦 پلن: {o.plan_title} — {fmt_price(o.price)} {config.CURRENCY}\n"
        f"🤖 ربات: @{o.bot_username}\n\n"
        f"📄 صفحه {page} از {total_pages}"
    )
    
    kb = admin_pending_page_kb(o.id, page, total_pages)
    
    # حذف پیام قبلی برای جلوگیری از خطای ویرایش نوع پیام (عکس به متن یا برعکس)
    await cb.message.delete()
    if o.receipt_file_id:
        await cb.message.answer_photo(o.receipt_file_id, caption=text, reply_markup=kb)
    else:
        await cb.message.answer(text, reply_markup=kb)


# ============================================================
# مدیریت اینستنس‌ها (یک پیام صفحه‌بندی‌شده با کیبورد تجمیعی)
# ============================================================
def admin_instances_kb(page_orders, current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    sizes = []
    
    for o in page_orders:
        if o.status == "failed":
            b.button(text=f"🔁 سعی مجدد {o.id}", callback_data=f"inst:retry:{o.id}")
        elif o.status in ("deployed", "running"):
            b.button(text=f"⏸ توقف {o.id}", callback_data=f"inst:stop:{o.id}")
        else:
            b.button(text=f"▶️ روشن {o.id}", callback_data=f"inst:start:{o.id}")
            
        b.button(text=f"💥 حذف {o.id}", callback_data=f"inst:destroy:{o.id}")
        b.button(text=f"🌐 پروکسی {o.id}", callback_data=f"ipx:menu:{o.id}", style="primary")
        sizes.append(3)  # سه دکمه در یک سطر: کنترل | حذف | پروکسی
        
    nav_buttons = 0
    if current_page > 1:
        b.button(text="⬅ قبلی", callback_data=f"adm:instpage:{current_page - 1}")
        nav_buttons += 1
    if current_page < total_pages:
        b.button(text="بعدی ➡️", callback_data=f"adm:instpage:{current_page + 1}")
        nav_buttons += 1
        
    if nav_buttons > 0:
        sizes.append(nav_buttons)
        
    b.button(text="↩️ بازگشت به پنل", callback_data="adm:panel")
    sizes.append(1)
    
    b.adjust(*sizes)
    return b.as_markup()

async def _instances_view(cb: CallbackQuery, page: int = 1):
    per_page = 6  # ۶ ربات در هر صفحه تا کیبورد بیش از حد بزرگ نشود (۱۴ دکمه)
    async with async_session() as session:
        orders = (await session.scalars(
            select(Order).where(Order.status.in_(("deployed", "stopped", "failed")))
            .order_by(Order.id.desc())
        )).all()

    if not orders:
        text = "هیچ اینستنسی وجود ندارد."
        try:
            if cb.message.photo:
                await cb.message.delete()
                await cb.message.answer(text, reply_markup=admin_panel_kb())
            else:
                await cb.message.edit_text(text, reply_markup=admin_panel_kb())
        except Exception:
            pass
        return

    total_pages = (len(orders) + per_page - 1) // per_page
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    page_orders = orders[start_idx:start_idx + per_page]

    lines = [f"🤖 <b>لیست اینستنس‌ها (صفحه {page}/{total_pages}):</b>\n"]
    for idx, o in enumerate(page_orders, start=1):
        try:
            st = await orchestrator.status(o.id)
            state = st.get("state", "؟")
        except Exception:
            state = "؟"

        status_icon = {"deployed": "🟢", "stopped": "⏸", "failed": "🔴"}.get(o.status, "⚪")
        line = (
            f"{idx}) {status_icon} <b>bot_{o.id}</b> — @{o.bot_username or '—'}\n"
            f"   └ وضعیت: {o.status} | داکر: <code>{state}</code>"
        )
        if o.expires_at:
            line += f" | انقضا: {o.expires_at.strftime('%Y/%m/%d')}"
        if o.status == "failed":
            safe_reason = html.escape((o.fail_reason or '')[:100])
            line += f"\n   └ <code>{safe_reason}</code>"
            
        lines.append(line)

    text = "\n\n".join(lines)
    kb = admin_instances_kb(page_orders, page, total_pages)
    
    try:
        # اگر از یک پیام متنی آمده باشیم، ویرایش می‌کنیم تا اسپم نشود
        if cb.message.photo:
            await cb.message.delete()
            await cb.message.answer(text, reply_markup=kb)
        else:
            await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "adm:instances")
async def cb_instances(cb: CallbackQuery):
    await cb.answer()
    await _instances_view(cb, 1)


@router.callback_query(F.data.startswith("adm:instpage:"))
async def cb_inst_page(cb: CallbackQuery):
    page = int(cb.data.split(":")[2])
    await cb.answer()
    await _instances_view(cb, page)


@router.callback_query(F.data.startswith("inst:stop:"))
async def cb_inst_stop(cb: CallbackQuery):
    oid = int(cb.data.split(":")[2])
    from core.deploy_queue import DeployJob, deploy_queue
    deploy_queue.submit(DeployJob(action="stop", order_id=oid))
    await cb.answer(f"bot_{oid} در صف توقف قرار گرفت")


@router.callback_query(F.data.startswith("inst:start:"))
async def cb_inst_start(cb: CallbackQuery):
    oid = int(cb.data.split(":")[2])
    from core.deploy_queue import DeployJob, deploy_queue
    deploy_queue.submit(DeployJob(action="start", order_id=oid))
    await cb.answer(f"bot_{oid} در صف روشن‌شدن قرار گرفت")


@router.callback_query(F.data.startswith("inst:destroy:"))
async def cb_inst_destroy(cb: CallbackQuery):
    parts = cb.data.split(":")
    oid = int(parts[2])
    
    if len(parts) == 4 and parts[3] == "yes":
        from core.deploy_queue import DeployJob, deploy_queue
        deploy_queue.submit(DeployJob(action="destroy", order_id=oid))
        await cb.answer("💥 در صف حذف کامل قرار گرفت")
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        return
        
    await cb.answer()
    await cb.message.answer(
        f"⚠️ <b>حذف کامل bot_{oid}</b>\n\n"
        "کانتینر + پوشه سشن‌ها + دیتابیس مشتری + اسلات redis — همه حذف می‌شود و "
        "<b>قابل بازگشت نیست!</b>",
        reply_markup=confirm_destroy_kb(oid),
    )


from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# اضافه شدن وضعیت جدید برای تأیید مبلغ
# 🆕 waiting_for_user = شارژ دستی از پنل (آیدی کاربر → مبلغ → تأیید)
class AdminChargeState(StatesGroup):
    waiting_for_user = State()
    waiting_for_amount = State()
    confirm_amount = State()


class AdminVideoState(StatesGroup):
    """🎬 آپلود ویدیوی آموزشی توکن از پنل ادمین."""
    waiting_media = State()


# ============================================================
# 🆕 شارژ دستی کاربر از پنل (نسخه ۳ — شارژ فقط از طریق پیوی پشتیبانی)
# ============================================================
@router.callback_query(F.data == "adm:chargeman")
async def cb_admin_charge_manual(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer(
        "💳 <b>شارژ حساب کاربر</b>\n\n"
        "👤 لطفاً <b>شناسه عددی (ID)</b> کاربر را بفرستید:\n"
        "<i>(کاربر شناسه خود را از صفحه «👤 پروفایل و موجودی» می‌تواند بردارد)</i>\n\n"
        "برای لغو /cancel را بفرستید."
    )
    await state.set_state(AdminChargeState.waiting_for_user)


@router.message(AdminChargeState.waiting_for_user, F.text)
async def st_admin_charge_user_id(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        await state.clear()
        return await message.answer("❌ عملیات لغو شد.")
    if not raw.isdigit() or not (5 <= len(raw) <= 12):
        return await message.answer("❌ شناسه عددی نامعتبر است. لطفاً فقط عدد ID کاربر را بفرستید (مثلاً 123456789):")

    target_user_id = int(raw)

    async with async_session() as session:
        balance = await session.scalar(
            text("SELECT balance FROM users WHERE tg_id = :uid"), {"uid": target_user_id}
        ) or 0

    await state.update_data(target_user_id=target_user_id)
    await message.answer(
        f"💰 <b>شارژ حساب کاربر</b> <code>{target_user_id}</code>\n\n"
        f"💵 موجودی فعلی کاربر: <b>{fmt_price(balance)} {config.CURRENCY}</b>\n\n"
        f"لطفاً <b>مبلغ</b> مورد نظر برای شارژ را به {config.CURRENCY} وارد کنید (مثلاً 3.5 یا 10):\n\n"
        "<i>برای لغو /cancel را بفرستید.</i>"
    )
    await state.set_state(AdminChargeState.waiting_for_amount)


@router.callback_query(F.data.startswith("adm:charge:"))
async def cb_admin_start_charge(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    target_user_id = int(parts[2])
    suggested_amount = int(parts[3]) if len(parts) > 3 else None
    
    from sqlalchemy import text
    from database import async_session
    
    # گرفتن موجودی لحظه‌ای کاربر از دیتابیس
    async with async_session() as session:
        balance = await session.scalar(text("SELECT balance FROM users WHERE tg_id = :uid"), {"uid": target_user_id}) or 0
        
    await state.update_data(target_user_id=target_user_id)
    
    msg = (
        f"💰 <b>شارژ حساب کاربر</b> <code>{target_user_id}</code>\n\n"
        f"💵 موجودی فعلی کاربر: <b>{fmt_price(balance)} {config.CURRENCY}</b>\n"
    )
    
    if suggested_amount:
        msg += f"💳 مبلغ ادعایی کاربر: <b>{fmt_price(suggested_amount)} {config.CURRENCY}</b>\n\n"
        msg += f"لطفاً <b>مبلغ نهایی</b> برای شارژ را به {config.CURRENCY} وارد کنید (مثلاً 3.5 یا 10 — یا همان مبلغ کاربر را تایپ کنید):"
    else:
        msg += f"\nلطفاً <b>مبلغ</b> مورد نظر برای شارژ را به {config.CURRENCY} وارد کنید (مثلاً 3.5 یا 10):"
        
    msg += "\n<i>برای لغو می‌توانید دستور /cancel را ارسال کنید.</i>"
    
    await cb.message.reply(msg)
    await state.set_state(AdminChargeState.waiting_for_amount)
    await cb.answer()


@router.message(AdminChargeState.waiting_for_amount, F.text)
async def st_admin_process_charge(message: Message, state: FSMContext):
    # ورودی دلاری (مثلاً 3.5) به سنت تبدیل می‌شود تا با ستون Integer دیتابیس سازگار بماند
    amount_cents = parse_to_cents(message.text)
    
    if amount_cents is None:
        return await message.answer(f"❌ مبلغ وارد شده نامعتبر است. لطفاً فقط عدد وارد کنید (مثلاً 3.5 یا 10):")
        
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    
    # ذخیره مبلغ (سنت) در State برای مرحله تأیید نهایی
    await state.update_data(amount=amount_cents)
    
    # ساخت کیبورد تأیید دو مرحله‌ای
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ بله، شارژ شود", callback_data="adm:confcharge:yes"),
            InlineKeyboardButton(text="❌ لغو", callback_data="adm:confcharge:no")
        ]
    ])
    
    await message.answer(
        f"❓ <b>تأیید شارژ حساب</b>\n\n"
        f"👤 کاربر: <code>{target_user_id}</code>\n"
        f"💰 مبلغ: <b>{fmt_price(amount_cents)} {config.CURRENCY}</b>\n\n"
        "آیا از صحت این مبلغ اطمینان دارید؟",
        reply_markup=kb
    )
    await state.set_state(AdminChargeState.confirm_amount)


@router.callback_query(AdminChargeState.confirm_amount, F.data.startswith("adm:confcharge:"))
async def cb_admin_confirm_charge(cb: CallbackQuery, state: FSMContext, bot: Bot):
    action = cb.data.split(":")[2]
    
    if action == "no":
        await cb.message.edit_text("❌ عملیات شارژ لغو شد.")
        await state.clear()
        return await cb.answer()
        
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    amount = data.get("amount")
    
    if not target_user_id or not amount:
        await cb.message.edit_text("❌ اطلاعات نشست منقضی شده است. لطفاً دوباره تلاش کنید.")
        return await state.clear()

    # اعمال قطعی مبلغ به دیتابیس مشتری
    from sqlalchemy import text
    from database import async_session
    
    try:
        async with async_session() as session:
            # شارژ با ORM (سازگار با MySQL و SQLite)
            from database.models import User
            u = await session.get(User, target_user_id)
            if u:
                u.balance = (u.balance or 0) + amount
            else:
                session.add(User(tg_id=target_user_id, balance=amount))
            await session.commit()

        # اطلاع‌رسانی به مشتری
        try:
            await bot.send_message(
                target_user_id,
                f"🎉 <b>حساب شما شارژ شد!</b>\n\n"
                f"مبلغ <b>{fmt_price(amount)} {config.CURRENCY}</b> با موفقیت به موجودی حساب شما افزوده شد. ✅"
            )
            msg_status = "پیام اطلاع‌رسانی به کاربر با موفقیت ارسال شد."
        except Exception as e:
            msg_status = f"⚠️ ارسال پیام به کاربر خطا داد (احتمالاً ربات را مسدود کرده است):\n<code>{e}</code>"
            
        await cb.message.edit_text(f"✅ شارژ <b>{fmt_price(amount)} {config.CURRENCY}</b> انجام شد!\n{msg_status}")
        
    except Exception as e:
        await cb.message.edit_text(f"🔴 خطای سرور در شارژ حساب:\n<code>{e}</code>")
        
    await state.clear()

# ============================================================
# 🆕 نسخه ۳ — حالت ساخت دستی (پشتیبانی ایمیج ربات را می‌سازد)
# mb:<order_id>:done    → پشتیبانی ربات را خارج از فکتوری ساخت؛ سفارش «تحویل‌شده» علامت می‌خورد
# mb:<order_id>:refund  → رد سفارش + عودت کامل وجه به کیف پول مشتری
# ============================================================
@router.callback_query(F.data.startswith("mb:"))
async def cb_manual_build(cb: CallbackQuery, bot: Bot):
    parts = cb.data.split(":")
    if len(parts) != 3:
        return await cb.answer("درخواست نامعتبر است.", show_alert=True)
    order_id, action = int(parts[1]), parts[2]

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order or order.status != "awaiting_approval":
            return await cb.answer("این سفارش قبلاً پردازش شده یا وجود ندارد.", show_alert=True)

        now_dt = dt.datetime.now(dt.timezone.utc)

        if action == "done":
            # ---------- ساخت دستی انجام شد ----------
            if order.renew_of:
                return await cb.answer("برای تمدید، از دکمه «✅ تأیید تمدید» استفاده کنید.", show_alert=True)

            order.status = "deployed"
            order.deployed_at = now_dt
            order.approved_at = order.approved_at or now_dt
            if not order.expires_at:
                order.expires_at = now_dt + dt.timedelta(days=order.duration_days or 30)
            await session.commit()

            bot_link = f"https://t.me/{order.bot_username.lstrip('@')}" if order.bot_username else "—"
            try:
                await bot.send_message(
                    order.tg_id,
                    "🎉 <b>ربات شما آماده شد!</b>\n\n"
                    f"🔗 لینک ربات: {bot_link}\n\n"
                    "۱) وارد ربات شوید و <b>/start</b> بزنید\n"
                    "۲) پنل مدیریت ظاهر می‌شود\n\n"
                    "موفق باشید! 🚀",
                )
            except Exception:
                pass

            summary = (
                f"✅ <b>سفارش #{order_id} (ساخت دستی) تحویل‌شده علامت خورد.</b>\n"
                f"🤖 ربات: @{order.bot_username}\n"
                f"⌛️ انقضا: {order.expires_at.strftime('%Y/%m/%d') if order.expires_at else '—'}"
            )
            try:
                if cb.message.photo:
                    await cb.message.edit_caption(caption=(cb.message.caption or "") + f"\n\n{summary}", reply_markup=None)
                else:
                    await cb.message.edit_text(summary, reply_markup=None)
            except Exception:
                pass
            return await cb.answer("تحویل ثبت شد ✅")

        if action == "refund":
            # ---------- رد + عودت کامل وجه (ORM — سازگار با MySQL و SQLite) ----------
            order.status = "rejected"
            from database.models import User
            u = await session.get(User, order.tg_id)
            if u:
                u.balance = (u.balance or 0) + order.price
            else:
                session.add(User(tg_id=order.tg_id, balance=order.price))
            await session.commit()

            try:
                await bot.send_message(
                    order.tg_id,
                    f"متأسفانه سفارش #{order_id} شما تأیید نشد.\n"
                    f"💰 مبلغ <b>{fmt_price(order.price)} {config.CURRENCY}</b> به کیف پول شما بازگردانده شد.\n"
                    f"برای بررسی بیشتر با پشتیبانی ({getattr(config, 'SUPPORT_USERNAME', 'مدیریت')}) در ارتباط باشید."
                )
            except Exception:
                pass

            try:
                if cb.message.photo:
                    await cb.message.edit_caption(caption=(cb.message.caption or "") + f"\n\n❌ <b>سفارش #{order_id} رد شد و وجه عودت داده شد</b>", reply_markup=None)
                else:
                    await cb.message.edit_text(f"❌ سفارش #{order_id} رد شد؛ {fmt_price(order.price)} {config.CURRENCY} به کیف پول مشتری عودت داده شد.", reply_markup=None)
            except Exception:
                pass
            return await cb.answer("رد شد و وجه عودت داده شد 💰")

    return await cb.answer("عملیات نامعتبر است.", show_alert=True)


# ============================================================
# 🆕 مدیریت ویدیوی آموزشی توکن (نسخه ۳)
# آپلود/حذف بدون ری‌استارت — از لحظه ذخیره، خودکار همراه متن‌های راهنما ارسال می‌شود
# ============================================================
@router.callback_query(F.data == "adm:video")
async def cb_video_panel(cb: CallbackQuery):
    from utils import media_store

    await cb.answer()
    media = await media_store.get_training_video()
    if media:
        file_id, mtype = media
        type_fa = {"video": "🎥 ویدیو", "animation": "🎞 گیف", "document": "📄 فایل"}.get(mtype, mtype)
        short = (file_id[:24] + "…") if len(file_id) > 24 else file_id
        status_text = (
            "🎬 <b>مدیریت ویدیوی آموزشی توکن</b>\n\n"
            f"✅ وضعیت: <b>فعال</b> ({type_fa})\n"
            f"🆔 file_id: <code>{short}</code>\n\n"
            "این ویدیو همراه «متن دکمه پنل‌ها» و «راهنمای دریافت توکن» برای کاربران ارسال می‌شود."
        )
    else:
        status_text = (
            "🎬 <b>مدیریت ویدیوی آموزشی توکن</b>\n\n"
            "❌ وضعیت: <b>ویدیویی تنظیم نشده است.</b>\n\n"
            "متن‌های راهنما در حال حاضر بدون ویدیو ارسال می‌شوند (بدون خطا).\n"
            "با آپلود ویدیو، از همان لحظه به‌صورت خودکار همراه متن‌ها ارسال خواهد شد. ✅"
        )

    from bot.keyboards import video_admin_kb
    try:
        await cb.message.edit_text(status_text, reply_markup=video_admin_kb(has_video=bool(media)))
    except Exception:
        await cb.message.answer(status_text, reply_markup=video_admin_kb(has_video=bool(media)))


@router.callback_query(F.data == "adm:videoup")
async def cb_video_upload(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer(
        "📤 <b>آپلود ویدیوی آموزشی توکن</b>\n\n"
        "لطفاً ویدیوی آموزشی را همین‌جا بفرستید:\n"
        "• 🎥 ویدیو (video)\n"
        "• 🎞 گیف (animation)\n"
        "• 📄 فایل ویدیویی (document)\n\n"
        "پس از دریافت، ذخیره می‌شود و از همان لحظه همراه متن‌های راهنما ارسال خواهد شد.\n"
        "<i>برای لغو /cancel را بفرستید.</i>"
    )
    await state.set_state(AdminVideoState.waiting_media)


@router.message(AdminVideoState.waiting_media, F.video | F.animation | F.document)
async def st_video_receive(message: Message, state: FSMContext, bot: Bot):
    from utils import media_store

    if message.video:
        file_id, mtype = message.video.file_id, "video"
    elif message.animation:
        file_id, mtype = message.animation.file_id, "animation"
    else:
        # فقط فایل‌های ویدیویی قبول می‌شوند
        mime = (message.document.mime_type or "").lower()
        if not mime.startswith("video/"):
            return await message.answer(
                "❌ این فایل ویدیویی نیست.\n"
                "لطفاً ویدیو را به‌صورت video، گیف یا فایل ویدیویی بفرستید."
            )
        file_id, mtype = message.document.file_id, "document"

    ok = await media_store.save_training_video(file_id, mtype)
    if not ok:
        return await message.answer("🔴 خطا در ذخیره ویدیو. لطفاً دوباره تلاش کنید.")

    await state.clear()
    type_fa = {"video": "🎥 ویدیو", "animation": "🎞 گیف", "document": "📄 فایل"}.get(mtype, mtype)
    await message.answer(
        f"✅ <b>ویدیوی آموزشی ذخیره شد!</b> ({type_fa})\n\n"
        "از این لحظه، ویدیو به‌صورت خودکار همراه «متن دکمه پنل‌ها» و «راهنمای دریافت توکن» ارسال می‌شود."
    )


@router.message(AdminVideoState.waiting_media)
async def st_video_invalid(message: Message):
    if (message.text or "").startswith("/"):
        return  # به /cancel اصلی سپرده می‌شود
    await message.answer(
        "❌ لطفاً خودِ ویدیو را بفرستید (video / گیف / فایل ویدیویی) — نه متن یا عکس."
    )


@router.callback_query(F.data == "adm:videodel")
async def cb_video_delete(cb: CallbackQuery, state: FSMContext):
    from utils import media_store

    await cb.answer()
    # اگر ادمین وسط آپلود بود، آن را هم لغو کن
    await state.clear()
    ok = await media_store.delete_training_video()
    if ok:
        await cb.message.edit_text(
            "🗑 <b>ویدیوی آموزشی حذف شد.</b>\n\n"
            "متن‌های راهنما از این پس بدون ویدیو ارسال می‌شوند (بدون هیچ خطایی)."
        )
    else:
        await cb.message.edit_text("🔴 خطا در حذف ویدیو.")
