# 🩹 پچ‌های سازگاری sender_bot با ربات‌ساز

این پوشه فایل‌هایی است که **قبل از build ایمیج** روی سورس پروژه Bulk Sender
کپی می‌شوند (اسکریپت `scripts/build_sender_image.sh` خودکار انجام می‌دهد).
فقط تغییرات ضروری برای اجرای چند-اینستنسی — بدون دست‌زدن به منطق بیزینس.

---

## ۱) `database/engine.py` — تله شماره ۲ (Too many connections)

**چرا؟** نسخه اصلی `pool_size=50, max_overflow=30` دارد (جمع ۸۰ کانکشن برای هر پروسه).
وقتی ۲۰+ اینستنس روی MySQL مشترک فکتوری می‌نشینند:

```
20 × 80 = 1600 کانکشن   >   max_connections=600   →  💥 قفل کل کارخانه
```

**تغییر:** مقادیر از متغیرهای محیطی `POOL_SIZE` و `MAX_OVERFLOW` خوانده می‌شوند
(پیش‌فرض 10 + 20 = ۳۰ برای هر اینستنس؛ ربات‌ساز همین اعداد را در `.env` هر
اینستنس می‌نویسد):

```
20 اینستنس × 30 = 600  ≤  max_connections=600  →  ✅ (برای ۱۰۰ اینستنس، pool را کمتر کنید)
```

بقیه فایل **دست‌نخورده** است. نسخه اصلی خودکار در `database/_original/` بکاپ می‌شود.

---

## ۲) پشتیبانی پروکسی در لاگین — ~~پچ لازم ندارد!~~ **پچ لازم دارد! 🩹**

> ⚠️ **به‌روزرسانی مهم (رفع باگ 1364):** ادعای قبلی این بخش («سورس شما از قبل کامل است»)
> دقیق نبود. INSERT خام فکتوری ستون‌های `fail_count` و `in_use` را نمی‌نوشت، در حالی که
> این ستون‌ها در مدل سندربِات NOT NULL و **بدون DEFAULT سطح دیتابیس** بودند ←
> MySQL 8 با `STRICT_TRANS_TABLES` هر درج جدید را با خطای
> `(1364, "Field 'fail_count' doesn't have a default value")` رد می‌کرد ←
> پروکسی هرگز به دیتابیس اینستنس نمی‌رسید و لاگین می‌گفت «پروکسی ندارید».

**سه‌لایه رفع (هر سه اعمال شده‌اند):**

| لایه | فایل | تغییر |
|---|---|---|
| ۱) فکتوری — INSERT | `core/proxy_manager.py` | ستون‌های `fail_count, in_use` با مقدار ۰ به INSERT اضافه شدند (شاخه `ON DUPLICATE KEY UPDATE` آن‌ها را دست نمی‌زند ← شمارنده‌های اینستنس محفوظ می‌مانند) |
| ۲) سندربِات — مدل | `database/models.py` | `server_default` برای `is_active/fail_count/in_use` ← دیتابیس‌های تازه (create_all) DEFAULT واقعی می‌گیرند |
| ۳) سندربِات — مهاجرت | `database/migrations.py` | بلوک `_COLUMN_DEFAULT_FIXES` در `run_startup_migrations` ← روی دیتابیس‌های موجود، `ALTER TABLE ... MODIFY ... DEFAULT` اجرا می‌شود (idempotent) |

| قابلیت | کجاست | کار ربات‌ساز |
|---|---|---|
| جدول `proxies` با `usage_type='login'` | `database/models.py` | ردیف‌های سالم را از استخر مرکزی **INSERT/UPDATE** می‌کند |
| انتخاب پروکسی تصادفی سالم هنگام لاگین | `bot/handlers/login_handlers.py` (خط ~447) | — خودش کار می‌کند |
| Fallback به `LOGIN_PROXY_URL` (env) | همان فایل | بهترین پروکسی را موقع ساخت در `.env` می‌نویسد |
| چرخش/کوئ/State-machine سلامت | `workers/session_manager.py` + `utils/health_checker.py` | — خودش کار می‌کند |

یعنی: **شما لیست پروکسی را در `/proxies` ربات فروش اضافه می‌کنید → فکتوری هر ۳ دقیقه
همه را تست TCP به DC تلگرام می‌کند → سالم‌ها را داخل جدول `proxies` دیتابیس هر مشتری
(با `usage_type='login'`) می‌نویسد → لاگین هر مشتری از همان‌جا برداشت می‌کند.**
مُرده‌ها `is_active=0, health_state='DEAD'` می‌شوند تا لاگین روی آن‌ها ننشیند، و
وقتی بهبود یابند خودکار برمی‌گردند.

---

## ۳) ویدیوی آموزشی (VIDEO_LINK) — رفع باگ منوی راهنما 🩹

**مشکل:** `render_instance_env` پارامتر `video_link` را می‌گرفت ولی در قالب `.env`
هرگز نمی‌نوشت؛ ضمناً کلید `VIDEO_LINK` در `.env` فکتوری هم تعریف نشده بود ←
دکمه «🎥 تماشای فیلم آموزش کار با ربات» در منوی راهنمای ربات مشتری با **URL خالی**
ساخته می‌شد ← تلگرام کل کیبورد را با `BUTTON_URL_INVALID` رد می‌کرد ←
**منوی راهنما اصلاً باز نمی‌شد.**

**رفع:**
- `templates/instance_env.py` ← خط `VIDEO_LINK={video_link}` به قالب اضافه شد؛
- `.env` / `.env.example` فکتوری ← کلید `VIDEO_LINK=` با راهنما اضافه شد؛
- سندربِات (`bot/handlers/general_handlers.py`) ← دکمه‌های URL خالی از کیبورد حذف می‌شوند (دفاع لایه دوم)؛
- `scripts/add_video_link.py` ← برای تزریق retroactive به `.env` اینستنس‌های **موجود**
  (`python scripts/add_video_link.py --apply` بعد از ست کردن VIDEO_LINK در فکتوری).

---

## نحوه اعمال

```bash
# سورس پروژه را یک بار روی سرور بگذارید:
rsync -a ./sender_bot_project/ /opt/factory/source/sender_bot/

# سپس (پچ + build با هم):
bash scripts/build_sender_image.sh
```
