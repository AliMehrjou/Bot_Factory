"""
فلوی سفارش مشتری — ربات فروش (نسخه ۳ — سفارش با ساخت دستی پشتیبانی)
================================================================
/start → سفارش سندر → انتخاب پلن (فقط با موجودی شارژ‌شده)
→ توکن ربات (اعتبارسنجی زنده getMe) → API_ID/API_HASH
→ کانال جوین اجباری (اختیاری) → خلاصه → پرداخت از کیف پول
→ ارجاع به پشتیبانی برای ساخت دستی ایمیج ربات (به‌جای دیپلوی خودکار)

شارژ حساب فقط از طریق پیوی پشتیبانی انجام می‌شود (صفحه «شارژ حساب» قیمت پلن‌ها
را نشان می‌دهد و کاربر را به پیوی پشتیبانی هدایت می‌کند).
"""
import logging
import time

from aiogram import Router, F, Bot, BaseMiddleware
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from sqlalchemy import select, func, text

# توجه: تابع mybots_kb باید طبق کدهای قبلی به bot.keyboards اضافه شده باشد.
from bot.keyboards import (plans_kb, skip_force_join_kb, confirm_summary_kb,
                           approval_kb, start_menu_kb, mybots_kb,
                           not_charged_kb, manual_build_kb)
from config import config, fmt_price
from core.orchestrator import validate_bot_token
from database import async_session
from database.models import Order
from utils.media_store import send_training_video

logger = logging.getLogger(__name__)
router = Router(name="user_order")


def _support_url() -> str | None:
    """آیدی پشتیبانی از .env → لینک مستقیم تلگرام."""
    u = (config.SUPPORT_USERNAME or "").strip().lstrip("@")
    return f"https://t.me/{u}" if u else None


WELCOME = (
    "🏭 <b>به ربات‌ساز فان سندر خوش آمدید!</b>\n\n"
    "اینجا می‌توانید <b>ربات ارسال انبوه اختصاصی خودتان (سندر)</b> را سفارش دهید.\n"
    "پس از ثبت سفارش و پرداخت، تیم پشتیبانی ربات شما را در سریع‌ترین زمان ممکن "
    "آماده کرده و لینک آن را تحویل می‌دهد. 🚀\n\n"
    "از منوی زیر انتخاب کنید:"
)

ORDER_INTRO = (
    "🛍 <b>سفارش سندر</b>\n\n"
    "✨ یکی از پلن‌های زیر را انتخاب کنید:\n\n"
    "💰 هر پلن شامل ربات اختصاصی + پشتیبانی کامل در طول اشتراک است.\n\n"
    "⚠️ برای انتخاب پلن، موجودی حساب شما باید معادل قیمت پلن باشد؛ "
    "در غیر این صورت ابتدا از بخش «💳 شارژ حساب»، حساب خود را از طریق پشتیبانی شارژ کنید."
)

WELCOME_HAS_BOTS = "🏭 <b>ربات‌ساز فان سندر</b>\n\n{count} ربات فعال دارید. سفارش جدید ثبت می‌کنید یا وضعیت ربات‌هایتان را می‌بینید؟"

GUIDE_TOKEN = (
    "🤖 <b>راهنمای ساخت ربات و دریافت توکن:</b>\n\n"
    "1️⃣ در تلگرام به <b>@BotFather</b> پیام بدهید\n\n"
    "2️⃣ دستور <code>/newbot</code> را بفرستید\n\n"
    "3️⃣ یک اسم نمایشی بنویسید (مثلاً «ربات ارسال من»)\n\n"
    "4️⃣ یک یوزرنیم با پسوند bot انتخاب کنید (مثلاً <code>my_sender_bot</code>)\n\n"
    "5️⃣ BotFather یک توکن شبیه این می‌دهد:\n"
    "🔑 <code>123456789:AAH4x...Tq0</code>\n\n"
    "🎥 توی ویدئو آموزشی ارسالی، نحوه گرفتن توکن ربات خودتون آموزش داده شده است.\n\n"
    "📩 آن توکن را بعد از پرداخت و شارژ موجودی خود، روی دکمه «سفارش سندر» می‌زنید، اشتراک خود را انتخاب می‌کنید "
    "(مثلاً: یک ماه) و بعدش دقیقاً همینجا ارسال می‌کنید. ✅\n\n"
    "⚡ و تمام! سندر شما کمتر از چند دقیقه ساخته میشه.\n\n"
    "⚠️ توکن ربات خودتون رو به هیچ وجه با هیچ‌کس به اشتراک نگذارید؛ چون دسترسی کامل پیدا می‌کنه به رباتتون. ⛔️"
)

# ============================================================
# 🆕 متن دکمه پنل‌ها (اشتراک‌ها) — متن رسمی کارفرما
# در صورتی که حساب شارژ بود و کاربر روی دکمه پلن زد، همین متن + ویدیوی آموزشی ارسال می‌شود.
# (توجه: متن دکمه «📖 راهنمای دریافت توکن» (GUIDE_TOKEN) با این متن فرق دارد و دست‌نخورده است.)
# ============================================================
PLAN_TOKEN_GUIDE = (
    "🤖 <b>راهنمای ساخت ربات و دریافت توکن:</b>\n\n"
    "1️⃣ در تلگرام به <b>@BotFather</b> پیام بدهید\n\n"
    "2️⃣ دستور <code>/newbot</code> را بفرستید\n\n"
    "3️⃣ یک اسم نمایشی بنویسید (مثلاً «ربات ارسال من»)\n\n"
    "4️⃣ یک یوزرنیم با پسوند bot انتخاب کنید (مثلاً <code>my_sender_bot</code>)\n\n"
    "5️⃣ BotFather یک توکن شبیه این می‌دهد:\n"
    "🔑 <code>123456789:AAH4x...Tq0</code>\n\n"
    "🎥 توی ویدئو آموزشی ارسالی، نحوه گرفتن توکن ربات خودتون آموزش داده شده است.\n\n"
    "📩 آن توکن را بعد از پرداخت و شارژ موجودی خود، روی دکمه «سفارش سندر» می‌زنید، "
    "اشتراک خود را انتخاب می‌کنید (مثلاً: یک ماه) و بعدش دقیقاً همینجا ارسال می‌کنید. ✅\n\n"
    "⚡ و تمام! سندر شما کمتر از چند دقیقه ساخته میشه.\n\n"
    "⚠️ توکن ربات خودتون رو به هیچ وجه با هیچ‌کس به اشتراک نگذارید؛ "
    "چون دسترسی کامل پیدا می‌کنه به رباتتون. ⛔️\n\n"
    "⭕️ لطفا الان همینجا توکن ربات خودتون رو بدون هیچ چیز اضافه ای بفرستید👇👇:"
)

NOT_ENOUGH_TEXT = (
    "💳 <b>موجودی حساب شما برای این پلن کافی نیست.</b>\n\n"
    "📦 پلن انتخابی: <b>{plan_title}</b> ({days} روز)\n"
    "💰 قیمت پلن: <b>{price} {currency}</b>\n"
    "💵 موجودی فعلی شما: <b>{balance} {currency}</b>\n\n"
    "📌 برای شارژ حساب، به پیوی پشتیبانی مراجعه کنید و طبق پلن مورد نظرتان هماهنگ کنید؛\n"
    "پس از شارژ، دوباره «🛍️ سفارش سندر» را بزنید. 🙏"
)

GUIDE_API = (
    "🔑 <b>راهنمای دریافت API_ID و API_HASH:</b>\n\n"
    "۱) به سایت <code>my.telegram.org</code> بروید و با شماره تلگرام خود وارد شوید\n"
    "۲) گزینه <b>API development tools</b> را بزنید\n"
    "۳) یک App title دلخواه بنویسید و Create کنید\n"
    "۴) دو مقدار <b>api_id</b> (عدد) و <b>api_hash</b> (رشته ۳۲ کاراکتری) به شما داده می‌شود\n\n"
    "اول <b>API_ID</b> (فقط عدد) را بفرستید."
)




class OrderFlow(StatesGroup):
    plan = State()
    bot_token = State()
    api_id = State()
    api_hash = State()
    force_join = State()
    receipt = State()


# ============================================================
# /start و منو
# ============================================================
async def _count_active_bots(tg_id: int) -> int:
    async with async_session() as session:
        return len((await session.scalars(
            select(Order).where(Order.tg_id == tg_id,
                                Order.status.in_(("deployed", "stopped")))
        )).all())


async def main_menu_text(tg_id: int) -> str:
    """متن منوی اصلی — هم برای /start و هم دکمه «بازگشت به منو» (usr:home)."""
    count = await _count_active_bots(tg_id)
    if count == 0:
        return WELCOME
    return WELCOME_HAS_BOTS.format(count=count)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    text = await main_menu_text(message.from_user.id)
    count = await _count_active_bots(message.from_user.id)
    await message.answer(text, reply_markup=start_menu_kb(has_orders=count > 0))


@router.callback_query(F.data == "ord:new")
async def cb_new(cb: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        pending_count = await session.scalar(
            select(func.count(Order.id)).where(
                Order.tg_id == cb.from_user.id, 
                Order.status == "awaiting_approval"
            )
        )
        max_pending = getattr(config, 'MAX_PENDING_ORDERS', 3)
        if pending_count >= max_pending:
            return await cb.answer(f"❌ شما {max_pending} سفارش در انتظار دارید. لطفاً تا بررسی آن‌ها منتظر بمانید.", show_alert=True)
            
    await state.clear()
    await cb.message.edit_text(ORDER_INTRO, reply_markup=plans_kb(config.PLANS))
    await cb.answer()
    await state.set_state(OrderFlow.plan)


async def _show_mybots(tg_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    async with async_session() as session:
        orders = (await session.scalars(
            select(Order).where(Order.tg_id == tg_id)
        )).all()
    if not orders:
        return "هنوز رباتی ندارید. با /start سفارش دهید. 🛒", None
        
    lines = ["🤖 <b>ربات‌های شما:</b>", ""]
    icons = {
        "deployed": "🟢", "stopped": "⏸", "expired": "⌛️",
        "awaiting_approval": "🛎", "rejected": "❌", "failed": "🔴", "provisioning": "⏳",
        "renewed": "♻️"
    }
    status_fa = {
        "awaiting_approval": "در انتظار تأیید", "rejected": "رد شده", 
        "failed": "نقص فنی", "provisioning": "در حال ساخت",
        "deployed": "فعال", "stopped": "متوقف", "expired": "منقضی",
        "renewed": "تمدید شده"
    }
    
    support_username = getattr(config, 'SUPPORT_USERNAME', '@support_admin')
    
    for o in orders:
        exp = f"تا {o.expires_at.strftime('%Y/%m/%d')}" if o.expires_at else status_fa.get(o.status, o.status)
        if o.status in ("rejected", "failed"):
            exp += f" (در صورت نیاز با {support_username} تماس بگیرید)"
        lines.append(f"{icons.get(o.status, '⚪')} @{o.bot_username or o.bot_title or 'نامشخص'} — {exp}")
        
    return "\n".join(lines), mybots_kb(orders)


@router.callback_query(F.data == "usr:mybots")
async def cb_mybots(cb: CallbackQuery):
    await cb.answer()
    text, kb = await _show_mybots(cb.from_user.id)
    await cb.message.answer(text, reply_markup=kb)


@router.message(Command("mybots"))
async def cmd_mybots(message: Message):
    text, kb = await _show_mybots(message.from_user.id)
    await message.answer(text, reply_markup=kb)


# هندلر «🎧 راهنما و پشتیبانی» (usr:help) به bot/handlers/info_pages.py منتقل شد
# (متن راهنمای جدید کارفرما در همان فایل — TEXT_HELP)


@router.callback_query(F.data == "ord:guide")
async def cb_guide(cb: CallbackQuery, bot: Bot):
    """دکمه «📖 راهنمای دریافت توکن» — متن دست‌نخورده + ویدیوی آموزشی (در صورت تنظیم بودن)."""
    await cb.answer()
    await cb.message.answer(GUIDE_TOKEN)
    # 🎬 ویدیوی آموزشی — اگر تنظیم نشده باشد بی‌صدا رد می‌شود (بدون هیچ خطایی)
    await send_training_video(bot, cb.from_user.id)


@router.callback_query(F.data == "ord:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    
    # به جای پیام لغو، کاربر را به منوی اصلی برمی‌‌گردانیم
    text = await main_menu_text(cb.from_user.id)
    count = await _count_active_bots(cb.from_user.id)
    
    await cb.message.edit_text(text, reply_markup=start_menu_kb(has_orders=count > 0))
    await cb.answer("بازگشت به منوی اصلی 🏠")


@router.message(Command("cancel"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ عملیات لغو شد. /start")


# ============================================================
# مرحله ۱: تمدید و انتخاب پلن
# ============================================================
@router.callback_query(F.data.startswith("ord:renew:"))
async def cb_renew(cb: CallbackQuery, state: FSMContext):
    order_id = int(cb.data.split(":")[2])
    
    async with async_session() as session:
        old_order = await session.get(Order, order_id)
        if not old_order:
            return await cb.answer("❌ این سفارش دیگر وجود ندارد.", show_alert=True)
        if old_order.tg_id != cb.from_user.id:
            return await cb.answer("❌ شما مجاز به تمدید این ربات نیستید.", show_alert=True)
            
    await state.clear()
    await state.update_data(renew_of=order_id)
    await cb.message.edit_text("🔄 <b>تمدید ربات</b>\n\nیکی از پلن‌های زیر را انتخاب کنید:", reply_markup=plans_kb(config.PLANS))
    await cb.answer()
    await state.set_state(OrderFlow.plan)


@router.callback_query(OrderFlow.plan, F.data.startswith("ord:plan:"))
async def cb_plan(cb: CallbackQuery, state: FSMContext, bot: Bot):
    key = cb.data.split(":")[2]
    plan = config.PLANS.get(key)
    if not plan:
        return await cb.answer("پلن نامعتبر است.", show_alert=True)

    data = await state.get_data()
    await state.update_data(plan_key=key, plan_title=plan["title"],
                            price=plan["price"], days=plan["days"])

    # ============================================================
    # 🆕 چک شارژ بودن حساب — پلن فقط با موجودی کافی قابل انتخاب است.
    # شارژ حساب از قبل و از طریق پیوی پشتیبانی انجام می‌شود.
    # ============================================================
    async with async_session() as session:
        balance = await session.scalar(
            text("SELECT balance FROM users WHERE tg_id = :uid"), {"uid": cb.from_user.id}
        ) or 0

    price = int(plan["price"])
    if balance < price:
        await cb.answer("⚠️ موجودی حساب شما کافی نیست", show_alert=True)
        await cb.message.answer(
            NOT_ENOUGH_TEXT.format(
                plan_title=plan["title"], days=plan["days"],
                price=fmt_price(price), currency=config.CURRENCY,
                balance=fmt_price(balance),
            ),
            reply_markup=not_charged_kb(_support_url()),
        )
        return  # کاربر در همان وضعیت انتخاب پلن می‌ماند؛ پس از شارژ دوباره تلاش می‌کند

    await cb.answer(f"پلن {plan['title']} انتخاب شد ✅")

    if data.get("renew_of"):
        async with async_session() as session:
            old_order = await session.get(Order, data["renew_of"])
            if not old_order:
                return await cb.answer("❌ سفارش مبدأ یافت نشد (احتمالاً حذف شده است).", show_alert=True)
            await state.update_data(
                bot_username=old_order.bot_username, bot_title=old_order.bot_title,
                bot_token=old_order.bot_token, api_id=old_order.api_id,
                api_hash=old_order.api_hash, force_join=old_order.force_join
            )
        await _show_summary(cb.message, state)
    else:
        # 🆕 حساب شارژ است → متن رسمی دکمه پنل‌ها + ویدیوی آموزشی (در صورت وجود)
        await cb.message.edit_text(PLAN_TOKEN_GUIDE)
        await send_training_video(bot, cb.from_user.id)
        await state.set_state(OrderFlow.bot_token)


# ============================================================
# دکمه‌های صفحات اطلاعاتی (چیه؟ / شارژ / اکانت مجازی / مزایا / قوانین)
# در bot/handlers/info_pages.py پیاده‌سازی شده‌اند؛ ord:new بالای همین فایل.
# ============================================================


# ============================================================
# مرحله ۲: توکن ربات (اعتبارسنجی زنده)
# ============================================================
@router.message(OrderFlow.bot_token, F.text)
async def st_bot_token(message: Message, state: FSMContext):
    token = message.text.strip()
    
    if token == config.SALES_BOT_TOKEN:
        return await message.answer("❌ این توکن ربات فروش است!")
        
    async with async_session() as session:
        exists = await session.scalar(
            select(Order).where(
                Order.bot_token == token, 
                # وضعیت destroyed به لیست استثنائات اضافه شد
                Order.status.notin_(('rejected', 'failed', 'destroyed'))
            )
        )
        if exists:
            return await message.answer("❌ این توکن از قبل در یک سفارش فعال ثبت شده است.")
            
    checking = await message.answer("🔍 در حال بررسی توکن از تلگرام...")
    result = await validate_bot_token(token)
    if not result["ok"]:
        await checking.edit_text(f"❌ {result['error']}\n\nدوباره توکن را بفرستید یا /cancel")
        return
        
    await state.update_data(
        bot_token=token,
        bot_username=result["username"],
        bot_title=result["title"],
    )
    await checking.edit_text(
        f"✅ ربات شناسایی شد: <b>{result['title']}</b> (@{result['username']})\n\n"
        "🔑 حالا API_ID را بفرستید (فقط عدد).\n\nاگر نمی‌دانید چیست، همین دستور /api را بزنید."
    )
    await state.set_state(OrderFlow.api_id)


@router.message(OrderFlow.api_id, Command("api"))
async def st_api_guide(message: Message):
    await message.answer(GUIDE_API)


# ============================================================
# مرحله ۳: API_ID
# ============================================================
@router.message(OrderFlow.api_id, F.text)
async def st_api_id(message: Message, state: FSMContext):
    raw = message.text.strip()
    if not raw.isdigit() or not (4 <= len(raw) <= 10):
        await message.answer("❌ API_ID فقط یک عدد است (معمولاً ۵ تا ۸ رقم). دوباره بفرستید یا /cancel")
        return
    await state.update_data(api_id=int(raw))
    await message.answer("✅ خوب است.\n\nحالا <b>API_HASH</b> را بفرستید (رشته ۳۲ کاراکتری حروف و اعداد).")
    await state.set_state(OrderFlow.api_hash)


# ============================================================
# مرحله ۴: API_HASH
# ============================================================
@router.message(OrderFlow.api_hash, F.text)
async def st_api_hash(message: Message, state: FSMContext):
    raw = message.text.strip()
    import re
    if not re.match(r"^[a-f0-9]{32}$", raw, re.IGNORECASE):
        await message.answer(
            "❌ API_HASH باید ۳۲ کاراکتر hexadecimal باشد (حروف a-f و اعداد). "
            "دوباره از my.telegram.org کپی کنید یا /cancel"
        )
        return
    await state.update_data(api_hash=raw.lower())
    await message.answer(
        "📢 <b>مرحله آخر قبل از پرداخت:</b>\n\n"
        "اگر می‌خواهید کاربران ربات شما مجبور به عضویت در یک کانال شوند، "
        "یوزرنیم کانال را بفرستید (مثل <code>@yourchannel</code>).\n"
        "اگر نمی‌خواهید، دکمه رد شدن را بزنید.",
        reply_markup=skip_force_join_kb(),
    )
    await state.set_state(OrderFlow.force_join)


@router.callback_query(OrderFlow.force_join, F.data == "ord:skipfj")
async def st_fj_skip(cb: CallbackQuery, state: FSMContext):
    await state.update_data(force_join="")
    await cb.answer()
    await _show_summary(cb.message, state)


@router.message(OrderFlow.force_join, F.text)
async def st_force_join(message: Message, state: FSMContext):
    raw = message.text.strip()
    if raw.startswith("@"):
        raw = raw[1:]
    if not raw or len(raw) > 64 or not all(c.isalnum() or c == "_" for c in raw):
        await message.answer("❌ فرمت کانال درست نیست. مثال صحیح: <code>@mychannel</code> — یا دکمه رد شدن.")
        return
    await state.update_data(force_join=f"@{raw}")
    await _show_summary(message, state)


# ============================================================
# خلاصه + پرداخت از کیف پول
# (شارژ حساب از قبل توسط پشتیبانی انجام شده؛ مسیر رسید حذف شده است)
# ============================================================
async def _show_summary(message: Message, state: FSMContext):
    data = await state.get_data()
    price = data.get('price', 0)

    tg_id = message.chat.id

    # دریافت موجودی کاربر از دیتابیس
    async with async_session() as session:
        balance = await session.scalar(text("SELECT balance FROM users WHERE tg_id = :uid"), {"uid": tg_id}) or 0

    is_renew = bool(data.get("renew_of"))
    plan_type = "تمدید اشتراک" if is_renew else "خرید ربات جدید"

    text_msg = (
        "🧾 <b>خلاصه سفارش شما:</b>\n\n"
        f"• نوع سفارش: <b>{plan_type}</b>\n"
        f"• پلن: <b>{data['plan_title']}</b> ({data['days']} روز)\n"
        f"• قیمت کل: <b>{fmt_price(price)} {config.CURRENCY}</b>\n"
        f"• ربات: @{data['bot_username']}\n"
        f"• کانال جوین اجباری: {data['force_join'] or 'ندارد'}\n\n"
        f"💰 موجودی کیف پول شما: <b>{fmt_price(balance)} {config.CURRENCY}</b>\n"
    )

    if balance >= price:
        text_msg += (
            f"✅ شما اعتبار کافی دارید؛ با ثبت سفارش، مبلغ <b>{fmt_price(price)} {config.CURRENCY}</b> "
            "از کیف پول شما کسر می‌شود.\n\n"
            "🔧 سفارش شما پس از پرداخت برای <b>تیم پشتیبانی</b> ارسال می‌شود و ربات شما "
            "توسط پشتیبانی ساخته و تحویل داده خواهد شد."
        )
    else:
        remaining = price - balance
        text_msg += (
            f"⚠️ کسری موجودی: <b>{fmt_price(remaining)} {config.CURRENCY}</b>\n\n"
            "📌 برای شارژ حساب، به پیوی پشتیبانی مراجعه کنید و طبق پلن مورد نظرتان هماهنگ کنید؛\n"
            "پس از شارژ، دوباره «🛍️ سفارش سندر» را بزنید. 🙏"
        )

    await message.answer(text_msg, reply_markup=confirm_summary_kb())
    await state.set_state(OrderFlow.receipt)

@router.callback_query(OrderFlow.receipt, F.data == "ord:pay_wallet")
async def cb_pay_wallet(cb: CallbackQuery, state: FSMContext, bot: Bot):
    # پاسخ فوری به تلگرام برای جلوگیری از گیر کردن دکمه
    await cb.answer("⏳ در حال پردازش پرداخت و ثبت سفارش...")

    try:
        data = await state.get_data()
        price = data.get("price", 0)
        is_renew = bool(data.get("renew_of"))

        import datetime as dt

        tg_id = cb.from_user.id

        async with async_session() as session:
            # ۱. بررسی تکراری نبودن سفارش
            exists = await session.scalar(
                select(Order).where(
                    Order.tg_id == tg_id,
                    Order.bot_token == data.get("bot_token", ""),
                    Order.status.notin_(('rejected', 'failed', 'destroyed'))
                )
            )
            if exists:
                return await cb.message.answer("❌ این سفارش قبلاً ثبت شده است.")

            # ۲. کسر اتمیک موجودی (رفع race دکمه پرداخت)
            res = await session.execute(
                text("UPDATE users SET balance = balance - :price WHERE tg_id = :uid AND balance >= :price"),
                {"price": price, "uid": tg_id},
            )
            if res.rowcount == 0:
                return await cb.message.answer(
                    "❌ موجودی شما کافی نیست!\n\n"
                    "📌 برای شارژ حساب، به پیوی پشتیبانی مراجعه کنید و طبق پلن مورد نظرتان هماهنگ کنید."
                )

            # ۳. ثبت سفارش
            #    MANUAL_BUILD_MODE=true  → «در انتظار ساخت توسط پشتیبانی» (بدون دیپلوی خودکار)
            #    MANUAL_BUILD_MODE=false → رفتار قدیمی (تأیید‌شده + صف ساخت خودکار)
            manual_mode = bool(getattr(config, "MANUAL_BUILD_MODE", True))
            order = Order(
                tg_id=tg_id,
                tg_username=cb.from_user.username or "",
                tg_name=cb.from_user.full_name or "",
                plan_key=data.get("plan_key", ""),
                plan_title=data.get("plan_title", ""),
                price=price,
                duration_days=data.get("days", 30),
                bot_token=data.get("bot_token", ""),
                bot_username=data.get("bot_username", ""),
                bot_title=data.get("bot_title", ""),
                api_id=data.get("api_id", 0),
                api_hash=data.get("api_hash", ""),
                admin_id=tg_id,
                force_join=data.get("force_join", ""),
                receipt_file_id="wallet_paid",
                status="awaiting_approval" if manual_mode else "approved",
                renew_of=data.get("renew_of"),
                approved_at=dt.datetime.now(dt.timezone.utc) if not manual_mode else None
            )
            session.add(order)
            await session.commit()
            order_id = order.id

        # ۴. پیام موفقیت به مشتری
        if manual_mode:
            await cb.message.edit_text(
                "✅ <b>پرداخت با موفقیت انجام شد!</b>\n\n"
                f"مبلغ <b>{fmt_price(price)} {config.CURRENCY}</b> از کیف پول شما کسر گردید.\n"
                f"🛎 سفارش <b>#{order_id}</b> ثبت شد و برای <b>تیم پشتیبانی</b> ارسال گردید.\n\n"
                "⏳ ربات شما به‌زودی توسط پشتیبانی ساخته شده و لینک آن همین‌جا برایتان ارسال می‌شود. 🚀"
            )
        else:
            await cb.message.edit_text(
                f"✅ <b>پرداخت با موفقیت انجام شد!</b>\n\n"
                f"مبلغ {fmt_price(price)} {config.CURRENCY} از کیف پول شما کسر گردید.\n"
                f"🚀 سفارش #{order_id} وارد صف ساخت شد و تا لحظاتی دیگر تحویل شما می‌شود."
            )
        await state.clear()

        # ۵. اطلاع به ادمین/پشتیبانی
        mention = f"@{cb.from_user.username}" if cb.from_user.username else (cb.from_user.full_name or str(tg_id))
        if manual_mode:
            admin_text = (
                f"🛎 <b>سفارش جدید #{order_id}</b>"
                + (" (تمدید اشتراک)" if is_renew else "") + "\n\n"
                f"👤 مشتری: {mention} (<code>{tg_id}</code>)\n"
                f"📦 پلن: {data.get('plan_title')} — {fmt_price(price)} {config.CURRENCY}\n"
                f"🤖 ربات: @{data.get('bot_username')}\n"
                f"📅 مدت: {data.get('days')} روز\n"
                f"💳 پرداخت: از کیف پول انجام شد (پرداخت تأییدشده)\n\n"
                "🔧 این سفارش در <b>حالت ساخت دستی</b> است؛ پس از آماده‌سازی ایمیج توسط پشتیبانی،\n"
                "وضعیت را با دکمه‌های زیر به‌روزرسانی کنید."
            )
            for admin_id in config.ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, admin_text,
                                           reply_markup=manual_build_kb(order_id, is_renew=is_renew))
                except Exception:
                    pass
            return  # ✋ در حالت ساخت دستی، هیچ دیپلوی خودکاری انجام نمی‌شود

        admin_text = (
            f"🟢 <b>فروش خودکار #{order_id} (کیف پول)</b>\n\n"
            f"👤 کاربر: <code>{cb.from_user.id}</code>\n"
            f"📦 پلن: {data.get('plan_title')} — مبلغ کسر شده: {fmt_price(price)} {config.CURRENCY}\n"
            f"🤖 ربات: @{data.get('bot_username')}\n"
            "این سفارش به‌صورت خودکار پرداخت و دیپلوی شد."
        )
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(admin_id, admin_text)
            except Exception:
                pass

        # ۶. ارسال به صف ساخت (فقط حالت خودکار)
        from core.deploy_queue import DeployJob, deploy_queue

        async def notify(result: dict):
            if result.get("ok"):
                text_msg = (
                    f"🎉 <b>ربات شما آماده شد!</b>\n\n"
                    f"🔗 لینک ربات: {result.get('url', f'https://t.me/' + data.get('bot_username'))}\n\n"
                    "وارد ربات شوید و <b>/start</b> بزنید."
                )
                try:
                    await bot.send_message(tg_id, text_msg)
                except Exception:
                    pass
            else:
                # عودت وجه
                async with async_session() as session2:
                    await session2.execute(
                        text("UPDATE users SET balance = balance + :price WHERE tg_id = :uid"),
                        {"price": price, "uid": tg_id}
                    )
                    await session2.commit()

                text_msg = "⚠️ متأسفانه در ساخت ربات خطای سرور رخ داد. مبلغ کسر شده به کیف پول شما بازگردانده شد."
                try:
                    await bot.send_message(tg_id, text_msg)
                except Exception:
                    pass

                import html
                safe_error = html.escape(str(result.get('error', ''))[:600])
                for admin_id in config.ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, f"🔴 <b>خطا در دیپلوی خودکار #{order_id}:</b>\n<code>{safe_error}</code>")
                    except Exception:
                        pass

        deploy_queue.submit(DeployJob(action="provision", order_id=order_id, done_cb=notify))

    except Exception as e:
        # اگر خطایی رخ دهد، اینجا به شما در ربات پیام می‌دهد تا متوجه باگ شویم
        await cb.message.answer(f"🔴 خطای سیستم در هنگام پردازش:\n<code>{str(e)}</code>")

# ============================================================
# 🆕 نسخه ۳ — فلوی «ارسال رسید» حذف شد:
# شارژ حساب فقط از طریق پیوی پشتیبانی انجام می‌شود و پلن‌ها فقط با موجودی
# کافی قابل انتخاب‌اند؛ بنابراین تنها مسیر پرداخت، کیف پول است (ord:pay_wallet).
# ============================================================


# ============================================================
# هندلر بازگشتی برای وضعیت‌های گمشده پس از ری‌استارت سیستم (I1)
# ============================================================
@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def st_fallback_restart(message: Message):
    text = message.text.strip()
    import re
    
    # جذب دقیق پیام‌های از دست رفته (فقط توکن، اعداد مربوط به API_ID و API_HASH)
    is_token = re.match(r"^[0-9]{5,10}:[a-zA-Z0-9_-]{30,}$", text)
    is_api_id = text.isdigit() and 4 <= len(text) <= 10
    is_api_hash = len(text) == 32 and re.match(r"^[a-f0-9]{32}$", text, re.IGNORECASE)
    
    if is_token or is_api_id or is_api_hash:
        await message.answer("🔄 نشست شما به دلیل به‌روزرسانی سیستم منقضی شده است. لطفاً عملیات خود را از /start مجدداً آغاز کنید.")

# ============================================================
# Throttling Middleware برای جلوگیری از اسپم کاربران
# ============================================================
class SimpleThrottleMiddleware(BaseMiddleware):
    def __init__(self, limit_sec: float = 1.0):
        self.limit_sec = limit_sec
        self.users = {}

    async def __call__(self, handler, event, data):
        # نادیده گرفتن آپدیت‌های غیرکاربری یا ادمین‌ها
        if not event.from_user or event.from_user.id in config.ADMIN_IDS:
            return await handler(event, data)
            
        now = time.time()
        if len(self.users) > 1000:
            self.users = {k: v for k, v in self.users.items() if now - v < self.limit_sec * 5}

        if event.from_user.id in self.users:
            if now - self.users[event.from_user.id] < self.limit_sec:
                # پاسخ به دکمه برای جلوگیری از القای حس خرابی ربات، و دراپ سایلنت برای پیام‌ها
                if isinstance(event, CallbackQuery):
                    await event.answer("⏳ لطفاً کمی آرام‌تر...")
                return
        self.users[event.from_user.id] = now
        return await handler(event, data)
     
# ثبت Middleware روی تمام پیام‌های مربوط به این Router
router.message.middleware(SimpleThrottleMiddleware())

