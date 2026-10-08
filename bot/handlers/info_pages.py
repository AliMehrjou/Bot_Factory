"""
📖 صفحات اطلاعاتی منوی اصلی — فان سندر
========================================
دکمه‌های منوی اصلی که صفحه اختصاصی دارند، در این فایل پیاده‌سازی شده‌اند:

  ❓ ربات سندر چیه؟        → callback: usr:whatis
  ✨ مزایای فان سندر       → callback: usr:benefits
  📜 قوانین ما (مهم)       → callback: usr:rules
  💳 شارژ حساب             → callback: usr:charge      (شماره کارت از .env)
  📱 نیاز به اکانت؟        → callback: usr:virtual     (معرفی تیم Plus Number)
  🎧 راهنما و پشتیبانی     → callback: usr:help
  📨 سفارش ارسال به Pv     → callback: usr:pv          (سفارش تبلیغات ارسال به پی‌وی + ۳ دکمه شیشه‌ای)
  📜 قوانین سفارش (PV)     → callback: usr:pvrules     (قوانین و مقررات سفارش ارسال به پی‌وی)
  👤 پروفایل و موجودی      → callback: usr:profile
  🏠 بازگشت به منو         → callback: usr:home        (بازسازی منوی اصلی)

⚠️ ویرایش متن‌ها: همه متن صفحات در بخش «متن صفحات» پایین همین فایل قرار دارد
   و بدون نیاز به دست زدن به منطق کد، قابل ویرایش است. تگ‌های HTML مجاز:
   <b> <i> <code> <u> <s>
   (جایگاه {support} در متن قوانین، خودکار با SUPPORT_USERNAME از .env جایگزین می‌شود)
"""
import html
import logging

from aiogram import Bot, Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, Message, InlineKeyboardMarkup,
                           InlineKeyboardButton)
from sqlalchemy import select, text

from bot.keyboards import start_menu_kb, info_page_kb, pv_order_kb, pv_rules_kb
from config import config, fmt_price, parse_to_cents
from database import async_session
from database.models import Order

logger = logging.getLogger(__name__)
router = Router(name="info_pages")


class ChargeFlow(StatesGroup):
    waiting_for_receipt = State()
    waiting_for_amount = State()


# ============================================================
# ابزارهای کمکی
# ============================================================
def _support_url() -> str | None:
    """آیدی پشتیبانی از .env → لینک مستقیم تلگرام (اگر تنظیم نشده باشد None)."""
    u = (config.SUPPORT_USERNAME or "").strip().lstrip("@")
    return f"https://t.me/{u}" if u else None


def _support_mention() -> str:
    """آیدی پشتیبانی به شکل @username برای نمایش داخل متن."""
    u = (config.SUPPORT_USERNAME or "").strip()
    return u if u.startswith("@") else f"@{u}"


async def _show_page(cb: CallbackQuery, text: str, kb) -> None:
    """نمایش صفحه با ویرایش همان پیام؛ اگر ویرایش ممکن نبود، پیام جدید ارسال می‌شود."""
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb)


# ============================================================
# متن صفحات — بخش قابل ویرایش توسط کارفرما
# (متن‌های رسمی ارسالی توسط کارفرما — 12/07/1405)
# ============================================================
TEXT_WHATIS = (
    "🤖 <b>ربات سندر چیه؟</b>\n\n"
    "📢 ربات سندر یا ربات ارسال به Pv، یک ربات تلگرامی و سیستم تبلیغاتی قدرتمند برای تلگرامه که به شما "
    "اجازه می‌ده چندین اکانت تلگرامی خودتون رو به سندر متصل کنید و با استفاده از اون‌ها بنر تبلیغات "
    "خودتون رو برای ممبرهای فعال و واقعی گروه‌های مختلف ارسال کنید.\n\n"
    "🎯 یعنی شما می‌تونید بنر تبلیغاتی: گروه، کانال، پیج اینستاگرام، سایت، لینک ویدیو، محصول، خدمات یا "
    "هر محتوای دیگه‌ای که دارید رو به مخاطبان فعال مختلف برسونید. 📢\n\n"
    "👥 سندر می‌تونه ممبرهای فعال گروه‌های تلگرامی رو جمع‌آوری و بر اساس فیلترهای مختلف دسته‌بندی کنه؛ "
    "مثلاً کاربران فعال، آنلاین‌های اخیر و... و ربات‌ها و موارد نامعتبر رو تا حد امکان از لیست حذف کنه. 🧹 "
    "و لیستی از واقعی‌ترین ممبرها رو بهتون بده.\n\n"
    "📨 بعد از آماده شدن لیست، سندر می‌تونه پیام تبلیغاتی شما رو برای اون ممبرها ارسال کنه؛ به‌صورت متن، "
    "عکس، بنر، لینک و سایر محتواهای تبلیغاتی.\n\n"
    "📊 علاوه بر این، داخل سندر می‌تونید آمار ارسال‌ها، تعداد موفق و ناموفق، وضعیت کمپین‌ها و تاریخچه "
    "فعالیت‌ها رو مشاهده و مدیریت کنید.\n\n"
    "⚡ <b>سندر اختصاصی خودت رو داشته باش!</b>\n\n"
    "🖥️ بعد از ثبت سفارش و تأیید پرداخت، سیستم به‌صورت خودکار سندر اختصاصی شما رو روی سرور آماده می‌کنه "
    "و لینک ربات در اختیارتون قرار می‌گیره. (کمتر از ۵ دقیقه)\n\n"
    "🚀 اگه می‌خوای تبلیغاتت رو در مقیاس بزرگ‌تر و حرفه‌ای‌تر انجام بدی، سندر می‌تونه ابزار اختصاصی "
    "تبلیغات تلگرامی خودت باشه.\n\n"
    "👇 برای شروع، روی «سفارش سندر» بزن. 👇"
)

TEXT_BENEFITS = (
    "⭐ <b>مزایای فان سندر</b>\n\n"
    "🔥 چرا فان سندر؟ چون تمام دغدغه‌های یک تبلیغات‌کار حرفه‌ای را حل کرده‌ایم:\n\n"
    "🔐 هر اکانتی که داخل ربات شما اد می‌کنید، جداگانه با پروکسی‌های معتبر و اختصاصی با کیفیت متصل "
    "می‌شود تا حداقل میزان فریز شدن اکانت و shadow ban و ریپورت شدن رو داشته باشن.\n\n"
    "📩 پشتیبانی از تمامی فرمت‌ها برای ارسال پیام تبلیغاتی: ویس، متن، عکس، ویدیو، لینک و...\n\n"
    "👥 قابلیت استخراج ممبرهای فعال تمامی گروه‌های تلگرامی، حتی گروه‌های ریکوستی و درخواست عضویت\n\n"
    "🖼️ قابلیت تنظیم عکس، بیو و اسم پروفایل کاملاً خودکار و حرفه‌ای\n\n"
    "🗂️ قابلیت مخزن بنرها برای تسریع در انجام سفارشات\n\n"
    "📢 قابلیت ارسال از کانال مبدأ خصوصی جهت ارسال بنرهای شما با ایموجی‌های پرمیوم\n\n"
    "📄 قابلیت ابزار تبدیل یوزرنیم‌هایی که دستی جمع کردین به فایل TXT و ارسال بنر تبلیغ برای آن‌ها\n\n"
    "🎯 قابلیت استخراج: ممبرهای آنلاین گروه، ممبرهای شماره‌دار، ممبرهای فعال چت، ممبرهای طلایی و "
    "ممبرهای فعال در ویسکال\n\n"
    "🌐 قابلیت اد کردن پروکسی‌های اختصاصی خود برای ارسال، جهت هرچه کم شدن مقدار فریز و بن شدن اکانت‌ها\n\n"
    "🧩 رابط کاربری بسیار ساده و روان ربات برای افراد خیلی مبتدی\n\n"
    "⚡ <b>تحویل آنی و خودکار</b>\n"
    "ربات شما بدون دخالت انسانی و در کمتر از ۱ دقیقه روی سرور ساخته و تحویل می‌شود.\n\n"
    "🔒 <b>داده‌های ایزوله و رمزنگاری‌شده</b>\n"
    "سشن‌ها و اطلاعات هر مشتری کاملاً جدا و امن نگهداری می‌شود؛ هیچ‌کس به داده‌های شما دسترسی ندارد.\n\n"
    "🛡 <b>ضد بن + استراحت هوشمند</b>\n"
    "اسکریپت ربات ما بیش از ۳۰ هزار خط کد نوشته شده و به همین سبب اکانت‌های شما کاملاً رفتار انسانی "
    "دارند و کمترین میزان دیلیت رو خواهند داشت.\n\n"
    "🧑‍💼 <b>بدون نیاز به دانش فنی</b>\n"
    "نه سرور می‌خواهید، نه کدنویسی؛ فقط سفارش بدهید و ربات اختصاصی‌تان را تحویل بگیرید.\n\n"
    "📊 <b>پنل مدیریت کامل</b>\n"
    "آمار لحظه‌ای، مدیریت اکانت‌ها، بنرها، دسته‌بندی‌ها و ابزار پاکسازی لیست — همه داخل خود ربات.\n\n"
    "🤝 <b>پشتیبانی واقعی</b>\n"
    "در تمام طول اشتراک کنار شما هستیم و تمدید فقط با یک دکمه انجام می‌شود.\n\n"
    "🔥 و در آخر اینو بگم: توی تلگرام ربات سندر داریم، طرف میگه ماهی ۳۰۰ تومن ولی غافل از اینکه بخاطر "
    "آماتور بودن برنامه‌نویسش باید هر هفته خدا تومن پول اکانت‌های جدید بدین؛ فرق اختصاصی ما اینه با اون‌ها.\n\n"
    "🚀 پس همین حالا شروع کن: «سفارش سندر فان سندر» 👇"
)

TEXT_RULES = (
    "📜 <b>قوانین ما (مهم)</b>\n\n"
    "⚠️ لطفاً پیش از ثبت سفارش، قوانین استفاده از سرویس را با دقت بخوانید. ⛔️\n"
    "ثبت سفارش به معنای <b>پذیرش کامل این قوانین</b> است:\n\n"
    "1️⃣ <b>مسئولیت استفاده</b>\n"
    "نحوه استفاده از ربات و مسئولیت محتواهای ارسالی کاملاً بر عهده خریدار است.\n\n"
    "2️⃣ <b>محتوای ممنوع</b> 🚫\n"
    "استفاده از ربات‌ساز سندر تیم فان سندر جهت ارسال محتوای غیرقانونی، توهین‌آمیز، سیاسی، کلاهبرداری، "
    "فیشینگ، محتوای جنسی و بزرگسالان و هر مطلب مغایر قوانین ایران و تلگرام ممنوع است. "
    "تخلف = قطع سرویس بدون هیچ‌گونه بازگشت وجه.\n\n"
    "3️⃣ <b>بن شدن اکانت‌ها</b> 🔒\n"
    "سندر فان سندر، بدون اغراق و بزرگنمایی، جدیدترین و بهترین ربات سندر در تلگرام ایران هست و با رعایت "
    "راهنما و قانون استراحت اکانت‌ها و نکاتی که در ویدئو آموزشی (که بعد از خرید ربات براتون ارسال میشه) "
    "گفته شده، ریسک بن و فریز شدن اکانت‌ها تا ۹۸ درصد پایین میاد؛ اما محدود شدن اکانت‌های متصل‌شده شما "
    "در تلگرام (به هر دلیلی) مشمول گارانتی یا بازگشت وجه نیست و این امری طبیعیست.\n\n"
    "4️⃣ <b>شخصی بودن سرویس</b> 👤\n"
    "بازفروش، اجاره یا اشتراک‌گذاری ربات و سرویس با دیگران ممنوع است و سرویس قطع خواهد شد.\n\n"
    "5️⃣ <b>تمدید و انقضا</b> ⏳\n"
    "اشتراک را قبل از انقضا تمدید کنید. پس از پایان مهلت، ربات متوقف می‌شود و در صورت عدم تمدید در بازه "
    "اعلام‌شده، تمامی داده‌های ربات حذف خواهند شد و قابل برگشت نیست.\n\n"
    "💤 <b>تغییر توکن و حذف ربات</b>\n"
    "در صورت حذف شدن ربات، تغییر توکن یا بروز هرگونه اختلال که نیاز به فعال‌سازی مجدد داشته باشد، "
    "نصب و راه‌اندازی مجدد با هزینه صورت می‌گیرد.\n\n"
    "6️⃣ <b>پرداخت‌ها</b> 💳\n"
    "به دلیل ماهیت دیجیتال محصول، پس از تأیید پرداخت و تحویل سرویس، بازگشت وجه امکان‌پذیر نیست.\n\n"
    "7️⃣ <b>رسید معتبر</b> 🧾\n"
    "ارسال رسید تکراری یا جعلی باعث مسدود شدن حساب کاربری می‌شود؛ پس لطفاً از فرستادن فیش فیک و "
    "فیشینگ خودداری کنید.\n\n"
    "8️⃣ <b>مرجع رسمی</b> 🛡️\n"
    "پشتیبانی و تغییرات فقط از طریق آیدی رسمی پشتیبانی ({support}) انجام می‌شود؛ مسئولیت معامله با "
    "افراد دیگر بر عهده مشتری است.\n\n"
    "❓ سوالی دارید؟ قبل از سفارش با پشتیبانی مطرح کنید. 🧑‍💻"
)

TEXT_HELP = (
    "ℹ️ <b>راهنما:</b>\n\n"
    "برای ساخت ربات سندر اختصاصی خودتون توسط تیم فان سندر، ابتدا از طریق دکمه «شارژ حساب» به پی‌وی "
    "پشتیبانی جهت افزایش اعتبار حساب خود مراجعه کنید.\n\n"
    "💳 بعد از پرداخت و افزایش اعتبار شما در ربات توسط پشتیبانی، می‌توانید با استفاده از دکمه "
    "«سفارش سندر»، ربات خودتون رو بسازید.\n\n"
    "• 🤖 ربات شما به‌صورت خودکار روی سرور اختصاصی ساخته می‌شود\n"
    "• 🔗 بعد از آماده شدن، لینک <code>t.me/YourBot</code> برایتان ارسال می‌شود\n"
    "• 👑 ادمین ربات خودتان هستید؛ بعد از اولین /start در رباتتان، پنل مدیریت ظاهر می‌شود\n"
    "• 🔒 سشن‌ها و داده‌های شما کاملاً ایزوله و رمزنگاری‌شده است"
)

TEXT_VIRTUAL = (
    "📱 <b>نیاز به اکانت تلگرامی دارید؟</b>\n\n"
    "چنانچه برای اضافه کردن اکانت‌های تلگرامی به ربات سندرتون نیاز به اکانت دارید و خودتون اکانت "
    "ندارید، می‌تونید از شماره‌های مجازی استفاده کنید.\n\n"
    "⚠️ <b>توجه:</b>\n"
    "تیم فان سندر هیچ‌گونه شماره مجازی نمی‌فروشد. اما:\n\n"
    "فقط بنا به معرفی دوستان و عزیزانی که از تیم <b>PLUS NUMBER</b> اکانت خریداری کرده بودند و از کیفیت "
    "اکانت‌ها بسیار رضایت داشتند، و قیمتشون از کل پنل‌های ایران پایین‌تره، ما هم تصمیم گرفتیم تیم فروش "
    "شماره مجازی Plus Number رو به شما معرفی کنیم.\n\n"
    "🔔 <b>نکته مهم:</b>\n"
    "تیم فروش شماره مجازی Plus Number کاملاً مستقل از تیم فان سندر فعالیت می‌کند و تیم فان سندر هیچ‌گونه "
    "پشتیبانی، مسئولیت یا تضمینی در این زمینه ارائه نمی‌دهد.\n\n"
    "📞 لطفاً در مورد شماره‌های مجازی، اکانت‌ها، قیمت، شرایط خرید و پشتیبانی مستقیماً از پشتیبانی تیم "
    "Plus Number سؤال کنید.\n"
    "❗ تیم فان سندر در این زمینه هیچ‌گونه پاسخگویی یا مسئولیتی ندارد."
)

# ---------- 📨 سفارش ارسال به Pv (متن رسمی کارفرما + ۳ دکمه شیشه‌ای) ----------
TEXT_PV_ORDER = (
    "📢 <b>سفارش تبلیغات ارسال به پی‌وی</b>\n\n"
    "💢 چنانچه تمایل به خرید ربات اختصاصی سندر (ارسال به PV) از تیم ما نبودید و خواستید ما بنر "
    "تبلیغاتی: گروه، کانال، پیج، چنل یوتیوب و... شمارو برای مخاطب‌های هدفتون ارسال کنیم، ما در اسرع "
    "وقت این کار رو براتون انجام میدیم. 🚀\n\n"
    "💰 هزینه ارسال هر ۱۰۰۰ عدد: <b>۴ دلار</b> میباشد\n\n"
    "📞 جهت مشاوره و سفارش روی دکمه «ارتباط با پشتیبانی» کلیک کنید و در پی‌وی پشتیبانی درخواست "
    "خودتونو شفاف اعلام کنید. 🆔\n\n"
    "⚠️ توجه: قبل از سفارش و ارسال درخواست به پشتیبانی، لطفاً با استفاده از دکمه قوانین، لیست قوانین "
    "و مقررات سفارش ارسال به پی‌وی تیم فان سندر رو حتماً مطالعه کنید. ⛔️"
)

# ---------- 📜 قوانین و مقررات سفارش ارسال به پی‌وی ----------
# (متن زیر بر اساس قوانین رسمی تیم فان سندر، تنظیم‌شده برای سرویس ارسال به پی‌وی است؛
#  در صورت ارسال متن رسمی جدید توسط کارفرما، فقط همین بلوک را جایگزین کنید.)
TEXT_PV_RULES = (
    "📜 <b>قوانین و مقررات سفارش ارسال به پی‌وی</b>\n\n"
    "⚠️ لطفاً پیش از ثبت سفارش ارسال به پی‌وی، قوانین زیر را با دقت بخوانید.\n"
    "ثبت سفارش به معنای <b>پذیرش کامل این قوانین</b> است:\n\n"
    "1️⃣ <b>مسئولیت محتوا</b>\n"
    "تعیین محتوای بنر تبلیغاتی (گروه، کانال، پیج، چنل یوتیوب، سایت، محصول یا خدمات) و مسئولیت آن "
    "کاملاً بر عهده سفارش‌دهنده است.\n\n"
    "2️⃣ <b>محتوای ممنوع</b> 🚫\n"
    "ارسال محتوای غیرقانونی، توهین‌آمیز، سیاسی، کلاهبرداری، فیشینگ، محتوای جنسی و بزرگسالان و هر مطلب "
    "مغایر قوانین ایران و تلگرام ممنوع است. تخلف = لغو سفارش و قطع همکاری بدون هیچ‌گونه بازگشت وجه.\n\n"
    "3️⃣ <b>هزینه و پرداخت</b> 💳\n"
    "هزینه ارسال هر ۱۰۰۰ عدد، <b>۴ دلار</b> است. سفارش شما پس از پرداخت کامل هزینه وارد صف ارسال "
    "می‌شود؛ به دلیل ماهیت دیجیتال سرویس، پس از شروع ارسال بازگشت وجه امکان‌پذیر نیست.\n\n"
    "4️⃣ <b>نحوه انجام سفارش</b> ⏳\n"
    "پس از تأیید پرداخت، بنر شما برای مخاطبان هدف (ممبرهای فعال و واقعی گروه‌ها) ارسال می‌شود و در "
    "پایان کار، گزارش آمار ارسال (موفق/ناموفق) به شما ارائه خواهد شد.\n\n"
    "5️⃣ <b>رسید معتبر</b> 🧾\n"
    "ارسال رسید تکراری یا جعلی باعث مسدود شدن حساب کاربری می‌شود؛ لطفاً از فرستادن فیش فیک و فیشینگ "
    "خودداری کنید.\n\n"
    "6️⃣ <b>مرجع رسمی</b> 🛡️\n"
    "ثبت سفارش و پشتیبانی فقط از طریق آیدی رسمی پشتیبانی ({support}) انجام می‌شود؛ مسئولیت معامله با "
    "افراد دیگر بر عهده مشتری است.\n\n"
    "❓ سوالی دارید؟ قبل از ثبت سفارش با پشتیبانی مطرح کنید. 🧑‍💻"
)


def _text_charge() -> str:
    """متن صفحه شارژ حساب — اطلاعات کارت از .env خوانده می‌شود."""
    holder = f"👤 به نام: <b>{config.PAYMENT_CARD_HOLDER}</b>\n" if config.PAYMENT_CARD_HOLDER else ""
    return (
        "💳 <b>شارژ حساب</b>\n\n"
        "برای پرداخت هزینه سفارش‌ها، حساب خود را از طریق کارت به کارت شارژ کنید:\n\n"
        f"💳 شماره کارت: <code>{config.PAYMENT_CARD}</code>\n"
        f"{holder}"
        f"💰 مبلغ: معادل {config.CURRENCY}یِ همان مبلغ سفارش، یا هر مبلغ موردنظر برای شارژ\n\n"
        f"📌 {config.PAYMENT_NOTE}\n\n"
        "✅ پس از واریز، <b>تصویر رسید</b> را در همین گفتگو ارسال کنید تا پرداخت تأیید و "
        "حسابتان شارژ شود.\n\n"
        "سوالی درباره پرداخت دارید؟ با پشتیبانی در ارتباط باشید. 🧑‍💻"
    )


# ============================================================
# هندلرهای صفحات
# ============================================================
@router.callback_query(F.data == "usr:whatis")
async def cb_whatis(cb: CallbackQuery):
    await cb.answer()
    await _show_page(cb, TEXT_WHATIS, info_page_kb(support_url=_support_url(), with_order=True))


@router.callback_query(F.data == "usr:benefits")
async def cb_benefits(cb: CallbackQuery):
    await cb.answer()
    await _show_page(cb, TEXT_BENEFITS, info_page_kb(support_url=_support_url(), with_order=True))


@router.callback_query(F.data == "usr:rules")
async def cb_rules(cb: CallbackQuery):
    await cb.answer()
    await _show_page(
        cb,
        TEXT_RULES.replace("{support}", _support_mention()),
        info_page_kb(support_url=_support_url()),
    )


@router.callback_query(F.data == "usr:help")
async def cb_help(cb: CallbackQuery):
    await cb.answer()
    await _show_page(cb, TEXT_HELP, info_page_kb(support_url=_support_url()))


@router.callback_query(F.data == "usr:charge")
async def cb_charge(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(ChargeFlow.waiting_for_receipt)
    await _show_page(cb, _text_charge(), info_page_kb(support_url=_support_url()))


@router.callback_query(F.data == "usr:virtual")
async def cb_virtual(cb: CallbackQuery):
    await cb.answer()
    await _show_page(cb, TEXT_VIRTUAL, info_page_kb(support_url=_support_url()))


# ============================================================
# 📨 سفارش ارسال به Pv — متن کارفرما + ۳ دکمه شیشه‌ای
#    (ارتباط با پشتیبانی | قوانین سفارش | بازگشت به منوی اصلی)
# ============================================================
@router.callback_query(F.data == "usr:pv")
async def cb_pv_order(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await _show_page(cb, TEXT_PV_ORDER, pv_order_kb(_support_url()))


@router.callback_query(F.data == "usr:pvrules")
async def cb_pv_rules(cb: CallbackQuery):
    await cb.answer()
    await _show_page(
        cb,
        TEXT_PV_RULES.replace("{support}", _support_mention()),
        pv_rules_kb(_support_url()),
    )


@router.callback_query(F.data == "usr:home")
async def cb_home(cb: CallbackQuery):
    """بازگشت به منوی اصلی — همان متن و کیبورد /start (با ویرایش همان پیام)."""
    from bot.handlers.user_order import main_menu_text

    await cb.answer()
    text = await main_menu_text(cb.from_user.id)
    kb = start_menu_kb(has_orders=await _has_active_bots(cb.from_user.id))
    await _show_page(cb, text, kb)


async def _has_active_bots(tg_id: int) -> bool:
    async with async_session() as session:
        rows = (await session.scalars(
            select(Order.id).where(
                Order.tg_id == tg_id,
                Order.status.in_(("deployed", "stopped")),
            )
        )).all()
    return len(rows) > 0


# ============================================================
# فلوی شارژ حساب (رسید + مبلغ → تأیید ادمین)
# ============================================================
@router.message(ChargeFlow.waiting_for_receipt, F.photo | F.document)
async def st_charge_receipt(message: Message, state: FSMContext):
    is_photo = bool(message.photo)
    file_id = message.photo[-1].file_id if is_photo else message.document.file_id

    # ذخیره فایل در وضعیت و رفتن به مرحله دریافت مبلغ
    await state.update_data(receipt_file_id=file_id, is_photo=is_photo)
    await message.answer(
        "✅ تصویر رسید دریافت شد.\n\n"
        f"💰 <b>لطفاً دقیقاً مبلغی که واریز کرده‌اید را به {config.CURRENCY} وارد کنید (فقط عدد):</b>\n"
        "<i>مثال: 3.5 یا 10</i>\n\n"
        "برای لغو /cancel را ارسال کنید."
    )
    await state.set_state(ChargeFlow.waiting_for_amount)


@router.message(ChargeFlow.waiting_for_receipt)
async def st_charge_invalid(message: Message):
    await message.answer("❌ لطفاً فقط <b>تصویر رسید واریز</b> را بفرستید (عکس یا فایل).")


@router.message(ChargeFlow.waiting_for_amount, F.text)
async def st_charge_amount(message: Message, state: FSMContext, bot: Bot):
    claimed_cents = parse_to_cents(message.text)
    if claimed_cents is None:
        return await message.answer("❌ لطفاً فقط عدد وارد کنید (مثلاً 3.5 یا 10):")

    data = await state.get_data()
    file_id = data.get("receipt_file_id")
    is_photo = data.get("is_photo")

    mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
    mention = html.escape(mention)

    # دریافت موجودی فعلی کاربر برای نمایش مستقیم به ادمین
    async with async_session() as session:
        balance = await session.scalar(
            text("SELECT balance FROM users WHERE tg_id = :uid"), {"uid": message.from_user.id}
        ) or 0

    admin_text = (
        f"💰 <b>درخواست شارژ حساب</b>\n\n"
        f"👤 کاربر: {mention} (<code>{message.from_user.id}</code>)\n"
        f"💵 موجودی فعلی کاربر: <b>{fmt_price(balance)} {config.CURRENCY}</b>\n"
        f"💳 مبلغ واریزی (اعلام شده توسط کاربر): <b>{fmt_price(claimed_cents)} {config.CURRENCY}</b>\n\n"
        "لطفاً پس از بررسی رسید، از طریق دکمه زیر حساب کاربر را شارژ کنید."
    )

    # ارسال مبلغ پیشنهادیِ کاربر (به سنت) به عنوان دیتا به دکمه ادمین
    charge_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💳 تأیید و شارژ ({fmt_price(claimed_cents)} {config.CURRENCY})",
            callback_data=f"adm:charge:{message.from_user.id}:{claimed_cents}",
        )]
    ])

    for admin_id in config.ADMIN_IDS:
        try:
            if is_photo:
                await bot.send_photo(admin_id, file_id, caption=admin_text, reply_markup=charge_kb)
            else:
                await bot.send_document(admin_id, file_id, caption=admin_text, reply_markup=charge_kb)
        except Exception:
            pass

    await message.answer(
        "✅ <b>رسید و مبلغ با موفقیت برای پشتیبانی ارسال شد.</b>\n"
        "پس از تأیید، حساب شما فوراً شارژ خواهد شد."
    )
    await state.clear()


# ============================================================
# پروفایل و موجودی
# ============================================================
@router.callback_query(F.data == "usr:profile")
async def cb_profile(cb: CallbackQuery):
    await cb.answer()

    async with async_session() as session:
        balance = await session.scalar(
            text("SELECT balance FROM users WHERE tg_id = :uid"), {"uid": cb.from_user.id}
        ) or 0

    text_msg = (
        f"👤 <b>پروفایل شما</b>\n\n"
        f"شناسه عددی: <code>{cb.from_user.id}</code>\n"
        f"💰 موجودی کیف پول: <b>{fmt_price(balance)} {config.CURRENCY}</b>\n\n"
        "با شارژ کیف پول می‌توانید سفارش‌های خود را در لحظه و بدون نیاز به تأیید ادمین ثبت کنید."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 شارژ حساب", callback_data="usr:charge")],
        [InlineKeyboardButton(text="🏠 بازگشت به منو", callback_data="usr:home")]
    ])

    await _show_page(cb, text_msg, kb)
