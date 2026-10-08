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


def confirm_summary_kb(can_pay_from_wallet: bool = True) -> InlineKeyboardMarkup:
    """خلاصه سفارش — شارژ حساب از قبل توسط پشتیبانی انجام شده؛
    پرداخت همیشه از کیف پول است (چون پلن فقط با موجودی کافی قابل انتخاب است)."""
    b = InlineKeyboardBuilder()
    b.button(text="💳 پرداخت از کیف پول و ثبت سفارش", callback_data="ord:pay_wallet", style="success")
    b.button(text="✖️ انصراف", callback_data="ord:cancel", style="danger")
    b.adjust(1, 1)
    return b.as_markup()


def not_charged_kb(support_url: str | None = None) -> InlineKeyboardMarkup:
    """پلن انتخاب شده ولی موجودی کافی نیست → هدایت به شارژ/پیوی پشتیبانی."""
    b = InlineKeyboardBuilder()
    b.button(text="💳 شارژ حساب", callback_data="usr:charge", style="success")
    if support_url:
        b.button(text="👨‍💻 ارتباط با پشتیبانی", url=support_url, style="primary")
    b.button(text="🔙 بازگشت به پلن‌ها", callback_data="ord:new", style="primary")
    b.adjust(1)
    return b.as_markup()


def manual_build_kb(order_id: int, is_renew: bool = False) -> InlineKeyboardMarkup:
    """سفارش پرداخت‌شده (کیف پول) در حالت ساخت دستی:
    - تمدید → فقط تأیید تمدید + رد/عودت
    - سفارش جدید → ساخت خودکار فکتوری / ساخت دستی انجام شد / رد و عودت"""
    b = InlineKeyboardBuilder()
    if is_renew:
        b.button(text="✅ تأیید تمدید", callback_data=f"appr:{order_id}:yes", style="success")
    else:
        b.button(text="🏗 تأیید و ساخت خودکار", callback_data=f"appr:{order_id}:yes", style="primary")
        b.button(text="🔧 ساخت دستی انجام شد", callback_data=f"mb:{order_id}:done", style="success")
        b.adjust(2)
    b.button(text="💰 رد و عودت وجه", callback_data=f"mb:{order_id}:refund", style="danger")
    b.adjust(*( [2] if not is_renew else [] ), 1)
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


def virtual_page_kb(virtual_bot_url: str | None = None) -> InlineKeyboardMarkup:
    """صفحه «📱 نیاز به اکانت تلگرامی؟» — طبق خواسته کارفرما، به‌جای دکمه پشتیبانی،
    دکمه شیشه‌ای لینک ربات خرید شماره مجازی نمایش داده می‌شود."""
    b = InlineKeyboardBuilder()
    if virtual_bot_url:
        b.button(text="🛒 خرید شماره مجازی | Plus Number", url=virtual_bot_url, style="primary")
    b.button(text="🏠 بازگشت به منوی اصلی", callback_data="usr:home", style="primary")
    b.adjust(1)
    return b.as_markup()


def charge_page_kb(support_url: str | None = None) -> InlineKeyboardMarkup:
    """صفحه «💳 شارژ حساب» — شارژ فقط از طریق پیوی پشتیبانی انجام می‌شود."""
    b = InlineKeyboardBuilder()
    if support_url:
        b.button(text="👨‍💻 ارتباط با پشتیبانی (شارژ حساب)", url=support_url, style="primary")
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
    b.button(text="💳 شارژ حساب کاربر", callback_data="adm:chargeman", style="success")
    b.button(text="🎬 ویدیوی آموزشی توکن", callback_data="adm:video", style="primary")
    b.adjust(2, 2, 2)
    return b.as_markup()


def video_admin_kb(has_video: bool) -> InlineKeyboardMarkup:
    """پنل مدیریت ویدیوی آموزشی توکن."""
    b = InlineKeyboardBuilder()
    if not has_video:
        b.button(text="📤 آپلود ویدیوی آموزشی", callback_data="adm:videoup", style="success")
    else:
        b.button(text="📤 جایگزینی با ویدیوی جدید", callback_data="adm:videoup", style="success")
        b.button(text="🗑 حذف ویدیو", callback_data="adm:videodel", style="danger")
        b.adjust(2)
    b.button(text="🔙 بازگشت به پنل مدیریت", callback_data="adm:panel", style="primary")
    b.adjust(*([2, 1] if has_video else [1, 1]))
    return b.as_markup()


# ---------------- پنل پروکسی ----------------
def proxy_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ افزودن به استخر مرکزی", callback_data="px:add", style="success")
    b.button(text="🗂 پروکسی اینستنس‌ها", callback_data="ipx:pick", style="success")
    b.button(text="⚡ تست سلامت (پینگ)", callback_data="px:check", style="primary")
    b.button(text="📑 لیست استخر مرکزی", callback_data="px:list", style="primary")
    b.button(text="🗑️ پاکسازی پروکسی‌های مرده", callback_data="px:purge", style="danger")
    b.button(text="📄 دریافت فایل متنی", callback_data="px:export", style="primary")
    b.button(text="🔄 همگام‌سازی دستی (Sync)", callback_data="px:sync", style="primary")
    b.button(text="🔙 بازگشت به مدیریت", callback_data="adm:panel", style="primary")
    b.adjust(2, 2, 2, 2, 1)
    return b.as_markup()


def proxy_detail_kb(proxy_id: int, is_disabled: bool) -> InlineKeyboardMarkup:
    """جزئیات یک پروکسی استخر مرکزی — تست/ویرایش/غیرفعال/حذف + استفاده."""
    b = InlineKeyboardBuilder()
    b.button(text="⚡ تست این پروکسی", callback_data=f"px:tst:{proxy_id}", style="primary")
    b.button(text="✏️ ویرایش (همه اینستنس‌ها)", callback_data=f"px:editask:{proxy_id}", style="primary")
    if is_disabled:
        b.button(text="🟢 فعال‌سازی", callback_data=f"px:enable:{proxy_id}", style="success")
    else:
        b.button(text="⚪ غیرفعال‌سازی", callback_data=f"px:disable:{proxy_id}", style="danger")
    b.button(text="🗑️ حذف از استخر", callback_data=f"px:delconf:{proxy_id}", style="danger")
    b.button(text="🔙 لیست استخر", callback_data="px:list", style="primary")
    b.adjust(2, 2, 1)
    return b.as_markup()


def proxy_list_manage_kb(proxies: list, usage_map: dict = None) -> InlineKeyboardMarkup:
    """لیست استخر مرکزی — هر ردیف کلیک‌پذیر → نمای جزئیات (تست/ویرایش/حذف/…)."""
    b = InlineKeyboardBuilder()
    sizes = []
    icons = {"active": "🟢", "weak": "🟡", "dead": "🔴", "disabled": "⚪"}
    from utils.proxy_parse import mask_proxy
    for p in proxies:
        cnt = ""
        if usage_map is not None:
            cnt = f" [{usage_map.get(p.id, 0)}🤖]"
        b.button(
            text=f"{icons.get(p.status, '⚪')} #{p.id} {mask_proxy(p.proxy_string)[:24]}"
                 f" {p.ping_ms if p.ping_ms is not None else '—'}ms{cnt}",
            callback_data=f"px:view:{p.id}", style="primary",
        )
        sizes.append(1)
    b.button(text="🔙 بازگشت", callback_data="px:menu", style="primary")
    sizes.append(1)
    b.adjust(*sizes)
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


# ---------------- 🎯 پنل پروکسی هر اینستنس ----------------
def instance_proxy_picker_kb(page_orders, page: int, total_pages: int, brief: dict) -> InlineKeyboardMarkup:
    """انتخاب اینستنس برای مدیریت پروکسی‌هایش (L=لاگین / S=سندر)."""
    b = InlineKeyboardBuilder()
    sizes = []
    for o in page_orders:
        cnt = brief.get(o.id, "L0/S0")
        b.button(
            text=f"🌐 bot_{o.id} — @{(o.bot_username or '—')[:14]} ({cnt})",
            callback_data=f"ipx:menu:{o.id}", style="primary",
        )
        sizes.append(1)
    nav = 0
    if page > 1:
        b.button(text="⬅️ قبلی", callback_data=f"ipx:pick:{page - 1}")
        nav += 1
    if page < total_pages:
        b.button(text="بعدی ➡️", callback_data=f"ipx:pick:{page + 1}")
        nav += 1
    if nav:
        sizes.append(nav)
    b.button(text="🔙 بازگشت به مدیریت پروکسی", callback_data="px:menu", style="primary")
    sizes.append(1)
    b.adjust(*sizes)
    return b.as_markup()


def instance_proxy_menu_kb(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ افزودن پروکسی جدید", callback_data=f"ipx:add:{order_id}", style="success")
    b.button(text="🎯 تخصیص از استخر مرکزی", callback_data=f"ipx:pool:{order_id}", style="success")
    b.button(text="📋 لیست پروکسی‌های اینستنس", callback_data=f"ipx:list:{order_id}:1", style="primary")
    b.button(text="⚡ تست همه پروکسی‌های اینستنس", callback_data=f"ipx:check:{order_id}", style="primary")
    b.button(text="🧹 حذف تخصیص‌های مرده", callback_data=f"ipx:rmdead:{order_id}", style="danger")
    b.button(text="💥 حذف همه تخصیص‌ها", callback_data=f"ipx:rmall:{order_id}", style="danger")
    b.button(text="📄 خروجی متنی اینستنس", callback_data=f"ipx:export:{order_id}", style="primary")
    b.button(text="🔄 سینک مجدد این اینستنس", callback_data=f"ipx:sync:{order_id}", style="primary")
    b.button(text="🔙 بازگشت به لیست اینستنس‌ها", callback_data="ipx:pick:1", style="primary")
    b.adjust(2, 2, 2, 2, 1, 1)
    return b.as_markup()


def instance_proxy_list_kb(order_id: int, rows, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """هر تخصیص یک دکمه — کلیک → نمای جزئیات با همه عملیات."""
    b = InlineKeyboardBuilder()
    sizes = []
    icons = {"active": "🟢", "weak": "🟡", "dead": "🔴", "disabled": "⚪"}
    ticons = {"login": "🔑", "sender": "📤", "both": "♻️"}
    from utils.proxy_parse import mask_proxy
    for ap, fp in rows:
        st_icon = icons.get(fp.status, "⚪")
        t_icon = ticons.get(ap.usage_type, "🔑")
        off = "" if ap.enabled else " ⏸"
        b.button(
            text=f"{st_icon}{t_icon} #{ap.id} {mask_proxy(fp.proxy_string)[:26]}{off}",
            callback_data=f"ipx:view:{order_id}:{ap.id}", style="primary",
        )
        sizes.append(1)
    nav = 0
    if page > 1:
        b.button(text="⬅️ قبلی", callback_data=f"ipx:list:{order_id}:{page - 1}")
        nav += 1
    if page < total_pages:
        b.button(text="بعدی ➡️", callback_data=f"ipx:list:{order_id}:{page + 1}")
        nav += 1
    if nav:
        sizes.append(nav)
    b.button(text="🔙 منوی اینستنس", callback_data=f"ipx:menu:{order_id}", style="primary")
    sizes.append(1)
    b.adjust(*sizes)
    return b.as_markup()


def instance_proxy_detail_kb(order_id: int, apid: int, enabled: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚡ تست این پروکسی", callback_data=f"ipx:tst:{order_id}:{apid}", style="primary")
    b.button(text="✏️ ویرایش آدرس/اعتبارنامه", callback_data=f"ipx:editask:{order_id}:{apid}", style="primary")
    b.button(text="🔀 تغییر نوع استفاده", callback_data=f"ipx:type:{order_id}:{apid}", style="primary")
    if enabled:
        b.button(text="⏸ غیرفعال‌سازی تخصیص", callback_data=f"ipx:off:{order_id}:{apid}", style="danger")
    else:
        b.button(text="▶️ فعال‌سازی تخصیص", callback_data=f"ipx:on:{order_id}:{apid}", style="success")
    b.button(text="🗑 حذف از این اینستنس", callback_data=f"ipx:rmask:{order_id}:{apid}", style="danger")
    b.button(text="🔙 لیست پروکسی‌ها", callback_data=f"ipx:list:{order_id}:1", style="primary")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def instance_proxy_type_kb(order_id: int, apid: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔑 فقط لاگین", callback_data=f"ipx:settype:{order_id}:{apid}:login", style="primary")
    b.button(text="📤 فقط سندر", callback_data=f"ipx:settype:{order_id}:{apid}:sender", style="primary")
    b.button(text="♻️ هردو", callback_data=f"ipx:settype:{order_id}:{apid}:both", style="success")
    b.button(text="✖️ انصراف", callback_data=f"ipx:view:{order_id}:{apid}", style="danger")
    b.adjust(3, 1)
    return b.as_markup()


def type_pick_kb(order_id: int, purpose: str) -> InlineKeyboardMarkup:
    """انتخاب نوع برای افزودن/تخصیص — purpose: add | pool"""
    b = InlineKeyboardBuilder()
    b.button(text="🔑 فقط لاگین", callback_data=f"ipx:{purpose}:{order_id}:login", style="primary")
    b.button(text="📤 فقط سندر", callback_data=f"ipx:{purpose}:{order_id}:sender", style="primary")
    b.button(text="♻️ هردو", callback_data=f"ipx:{purpose}:{order_id}:both", style="success")
    b.button(text="✖️ انصراف", callback_data=f"ipx:menu:{order_id}", style="danger")
    b.adjust(3, 1)
    return b.as_markup()


def confirm_instance_rm_kb(order_id: int, apid: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✔️ بله، حذف شود", callback_data=f"ipx:rmask:{order_id}:{apid}:yes", style="danger")
    b.button(text="✖️ انصراف", callback_data=f"ipx:view:{order_id}:{apid}", style="primary")
    b.adjust(2)
    return b.as_markup()


def confirm_instance_rmall_kb(order_id: int, only_dead: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if only_dead:
        b.button(text="🧹 بله، مرده‌ها حذف شوند", callback_data=f"ipx:rmdead:{order_id}:yes", style="danger")
        b.button(text="✖️ انصراف", callback_data=f"ipx:menu:{order_id}", style="primary")
    else:
        b.button(text="💥 بله، همه حذف شوند", callback_data=f"ipx:rmall:{order_id}:yes", style="danger")
        b.button(text="✖️ انصراف", callback_data=f"ipx:menu:{order_id}", style="primary")
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