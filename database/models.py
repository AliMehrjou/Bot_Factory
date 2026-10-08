"""مدل‌های دیتابیس مرکزی ربات‌ساز"""
import datetime as dt
from sqlalchemy import String, Integer, BigInteger, Boolean, DateTime, Text, UniqueConstraint, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """
    اطلاعات مشتریان (برای سیستم کیف پول و شارژ حساب)
    """
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    balance: Mapped[int] = mapped_column(BigInteger, default=0)


class Order(Base):
    """
    سفارش مشتری — یک ردیف = یک اینستنس بالقوه.
    چرخه وضعیت:
      awaiting_approval <- approved <- provisioning <- deployed <- (stopped <<- running) <- destroyed
                         <- rejected | expired | failed
    """
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # مشتری
    tg_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    tg_username: Mapped[str] = mapped_column(String(64), default="")
    tg_name: Mapped[str] = mapped_column(String(128), default="")

    # پلن و پرداخت
    plan_key: Mapped[str] = mapped_column(String(32), default="")
    plan_title: Mapped[str] = mapped_column(String(64), default="")
    price: Mapped[int] = mapped_column(Integer, default=0)
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    receipt_file_id: Mapped[str] = mapped_column(String(255), default="")  # file_id تصویر رسید در تلگرام

    # مشخصات ربات مشتری
    bot_token: Mapped[str] = mapped_column(String(255), nullable=False)
    bot_username: Mapped[str] = mapped_column(String(64), default="")
    bot_title: Mapped[str] = mapped_column(String(128), default="")
    api_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    api_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    admin_id: Mapped[int] = mapped_column(BigInteger, default=0)
    force_join: Mapped[str] = mapped_column(String(255), default="")  # @channel یا خالی

    # وضعیت
    status: Mapped[str] = mapped_column(String(24), default="awaiting_approval", index=True)
    fail_reason: Mapped[str] = mapped_column(Text, default="")
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renew_of: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # زیرساخت
    redis_slot: Mapped[int] = mapped_column(Integer, nullable=True)
    fernet_key: Mapped[str] = mapped_column(String(255), default="")  # نسخه پشتیبان کلید .env

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deployed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FactoryProxy(Base):
    """
    پروکسی مرکزی ربات‌ساز.
    فرمت proxy_string مطابق sender_bot (بدون scheme): user:pass@host:port یا host:port
    """
    __tablename__ = "factory_proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheme: Mapped[str] = mapped_column(String(10), default="socks5")
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(128), default="")
    password: Mapped[str] = mapped_column(String(128), default="")

    proxy_string: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|weak|dead|disabled
    ping_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    consec_ok: Mapped[int] = mapped_column(Integer, default=0)
    consec_fail: Mapped[int] = mapped_column(Integer, default=0)
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class InstanceProxy(Base):
    """
    🎯 تخصیص پروکسی استخر مرکزی به هر اینستنس (خواسته کارفرما):
    ادمین از ربات‌ساز برای «هر اینستنس» پروکسی لاگین/سندر اضافه می‌کند،
    حذف/ویرایش/تست می‌گیرد و مشتری (کاربر نهایی) هیچ دخالی ندارد.

    - یک ردیف = تخصیص یک پروکسی به یک اینستنس با نوع استفاده مشخص
    - usage_type:  login (فقط لاگین) | sender (فقط سندر/ارسال) | both (هردو)
    - enabled:     خاموش/روشن کردن تخصیص بدون حذف (per-instance)
    - منبع حقیقتِ سینک: هر اینستنس فقط پروکسی‌های «تخصیص‌یافته و روشن» خودش را
      در جدول proxies دیتابیس خودش می‌گیرد (به‌جای چرخش عمومی قدیمی).
    - اینستنس‌هایی که هنوز هیچ تخصیصی ندارند، در حالت legacy از چرخش عمومی
      استخر سالم استفاده می‌کنند (سازگاری با قبل از این نسخه).
    """
    __tablename__ = "instance_proxies"
    __table_args__ = (
        UniqueConstraint("order_id", "proxy_id", name="uq_order_proxy"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    proxy_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    usage_type: Mapped[str] = mapped_column(String(10), default="login")  # login | sender | both
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    note: Mapped[str] = mapped_column(String(255), default="")  # یادداشت اختیاری ادمین
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RedisSlot(Base):
    """نقشه اسلات‌های Redis: هر اینستنس یک (کانتینر، db منطقی) منحصربه‌فرد می‌گیرد."""
    __tablename__ = "redis_slots"
    slot_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)


class SyncLog(Base):
    """آخرین نتیجه سینک پروکسی برای هر اینستنس (برای گزارش پنل ادمین)."""
    __tablename__ = "sync_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AppSetting(Base):
    """
    ⚙️ تنظیمات پویای ربات (key/value) — بدون نیاز به ری‌استارت:
    - token_guide_video → فایل‌آیدی ویدیوی آموزشی توکن
      (آپلود از پنل ادمین؛ از لحظه ذخیره، خودکار همراه متن‌های راهنما ارسال می‌شود)
    """
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)