"""کیبوردهای inline ربات فروش"""
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import config, fmt_price


def plans_kb(plans: dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, p in plans.items():
        b.button(
            text=f"📅 اشتراک {p['title']} ◂ {fmt_price(p['price'])} {config.CURRENCY}",
            callback_data=f"ord:plan:{key}", style="success",
        )
    
    b.button(text="📖 راهنمای دریافت توکن", callback_data="ord:guide", style="primary")
    # دکمه بازگشت جدید
    b.button(text="🔙 بازگشت به منو", callback_data="ord:cancel", style="danger")
    
    b.adjust(1)
    return b.as_markup()


def skip_force_join_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⏭️ رد شدن (بدون کانال اجباری)", callback_data="ord:skipfj", style="primary")
    return b.as_markup()


def confirm_summary_kb(can_pay_from_wallet: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if can_pay_from_wallet:
        b.button(text="💳 پرداخت از کیف پول و ساخت ربات", callback_data="ord:pay_wallet", style="success")
        b.button(text="✖️ انصراف", callback_data="ord:cancel", style="danger")
        b.adjust(1, 1)
    else:
        b.button(text="✅ تأیید و ارسال رسید", callback_data="ord:confirm", style="success")
        b.button(text="✖️ انصراف", callback_data="ord:cancel", style="danger")
        b.adjust(2)
    return b.as_markup()


def approval_kb(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ تأیید و دیپلوی", callback_data=f"appr:{order_id}:yes", style="success")
    b.button(text="⛔️ رد کردن", callback_data=f"appr:{order_id}:no", style="danger")
    b.adjust(2)
    return b.as_markup()


def start_menu_kb(has_orders: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🛍️ سفارش سندر", callback_data="ord:new", style="success")
    b.button(text="📨 سفارش ارسال به Pv", callback_data="usr:pv", style="success")
    if has_orders:
        b.button(text="🤖 ربات‌های من", callback_data="usr:mybots", style="primary")
    b.button(text="👤 پروفایل و موجودی", callback_data="usr:profile", style="primary")
    b.button(text="❓ ربات سندر چیه؟", callback_data="usr:whatis", style="primary")
    b.button(text="✨ مزایای فان سندر", callback_data="usr:benefits", style="primary")
    b.button(text="💳 شارژ حساب", callback_data="usr:charge", style="primary")
    b.button(text="📱 نیاز به اکانت تلگرامی؟", callback_data="usr:virtual", style="primary")
    b.button(text="⚖️ قوانین ما (مهم)", callback_data="usr:rules", style="primary")
    b.button(text="🎧 راهنما و پشتیبانی", callback_data="usr:help", style="primary")

    # چیدمان: سفارش سندر و سفارش ارسال به Pv هرکدام سطر جدا، بقیه دونفره
    if has_orders:
        b.adjust(1, 1, 2, 2, 2, 2)
    else:
        b.adjust(1, 1, 2, 2, 2, 1)
    return b.as_markup()


def info_page_kb(support_url: str | None = None, with_order: bool = False) -> InlineKeyboardMarkup:
    """کیبورد پایین صفحات اطلاعاتی"""
    b = InlineKeyboardBuilder()
    if with_order:
        b.button(text="🛍️ سفارش سندر", callback_data="ord:new", style="success")
    if support_url:
        b.button(text="👨‍💻 ارتباط با پشتیبانی", url=support_url, style="primary")
    b.button(text="🏠 بازگشت به منوی اصلی", callback_data="usr:home", style="primary")
    b.adjust(1)
    return b.as_markup()


def pv_order_kb(support_url: str | None = None) -> InlineKeyboardMarkup:
    """صفحه «سفارش ارسال به Pv» — دقیقاً ۳ دکمه شیشه‌ای چسبیده به پیام (طبق سفارش کارفرما)"""
    b = InlineKeyboardBuilder()
    if support_url:
        b.button(text="👨‍💻 ارتباط با پشتیبانی", url=support_url, style="primary")
    b.button(text="📜 قوانین سفارش", callback_data="usr:pvrules", style="primary")
    b.button(text="🏠 بازگشت به منوی اصلی", callback_data="usr:home", style="primary")
    b.adjust(1)
    return b.as_markup()


def pv_rules_kb(support_url: str | None = None) -> InlineKeyboardMarkup:
    """صفحه «قوانین سفارش ارسال به پی‌وی» — بازگشت به صفحه سفارش PV و منوی اصلی"""
    b = InlineKeyboardBuilder()
    if support_url:
        b.button(text="👨‍💻 ارتباط با پشتیبانی", url=support_url, style="primary")
    b.button(text="📨 بازگشت به سفارش ارسال به پی‌وی", callback_data="usr:pv", style="primary")
    b.button(text="🏠 بازگشت به منوی اصلی", callback_data="usr:home", style="primary")
    b.adjust(1)
    return b.as_markup()


# ---------------- پنل ادمین ----------------
def admin_panel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📥 سفارش‌های در انتظار", callback_data="adm:pending", style="success")
    b.button(text="🎛 اینستنس‌ها (ربات‌ها)", callback_data="adm:instances", style="primary")
    b.button(text="🛡️ مدیریت پروکسی‌ها", callback_data="px:menu", style="primary")
    b.button(text="📈 آمار و گزارش‌ها", callback_data="adm:stats", style="primary")
    b.adjust(2, 2)
    return b.as_markup()


# ---------------- پنل پروکسی ----------------
def proxy_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ افزودن پروکسی", callback_data="px:add", style="success")
    b.button(text="⚡ تست سلامت (پینگ)", callback_data="px:check", style="primary")
    b.button(text="📑 لیست پروکسی‌ها", callback_data="px:list", style="primary")
    b.button(text="🗑️ پاکسازی پروکسی‌های مرده", callback_data="px:purge", style="danger")
    b.button(text="📄 دریافت فایل متنی", callback_data="px:export", style="primary")
    b.button(text="🔄 همگام‌سازی دستی (Sync)", callback_data="px:sync", style="primary")
    b.button(text="🔙 بازگشت به مدیریت", callback_data="adm:panel", style="primary")
    b.adjust(2, 2, 2, 1)
    return b.as_markup()


def proxy_list_manage_kb(proxies: list) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    adjust_pattern = []
    for p in proxies:
        b.button(text=f"🗑️ حذف #{p.id}", callback_data=f"px:delconf:{p.id}", style="danger")
        if p.status == "disabled":
            b.button(text=f"🔴 فعال‌سازی #{p.id}", callback_data=f"px:enable:{p.id}", style="success")
        else:
            b.button(text=f"🟢 غیرفعال‌سازی #{p.id}", callback_data=f"px:disable:{p.id}", style="danger")
        adjust_pattern.append(2)
    b.button(text="🔙 بازگشت", callback_data="px:menu", style="primary")
    adjust_pattern.append(1)
    b.adjust(*adjust_pattern)
    return b.as_markup()


def confirm_proxy_del_kb(proxy_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✔️ بله، حذف شود", callback_data=f"px:del:{proxy_id}", style="danger")
    b.button(text="✖️ انصراف", callback_data="px:list", style="primary")
    b.adjust(2)
    return b.as_markup()


def proxy_row_del_kb(proxy_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"🗑️ حذف نهایی #{proxy_id}", callback_data=f"px:del:{proxy_id}", style="danger")
    return b.as_markup()


def confirm_purge_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🧹 بله، پاکسازی کن", callback_data="px:purge:yes", style="danger")
    b.button(text="✖️ انصراف", callback_data="px:menu", style="primary")
    b.adjust(2)
    return b.as_markup()


# ---------------- اینستنس‌ها ----------------
def confirm_destroy_kb(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚠️ بله، کاملاً نابود کن", callback_data=f"inst:destroy:{order_id}:yes", style="danger")
    b.button(text="✖️ بازگشت", callback_data="adm:instances", style="primary")
    b.adjust(2)
    return b.as_markup()

def mybots_kb(orders: list) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for o in orders:
        if o.status in ("deployed", "stopped", "expired"):
            b.button(text=f"🔋 تمدید اشتراک @{o.bot_username or o.bot_title}", callback_data=f"ord:renew:{o.id}", style="success")
    b.adjust(1)
    return b.as_markup()