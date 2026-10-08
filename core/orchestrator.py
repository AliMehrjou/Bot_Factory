"""
🔧 Orchestrator — موتور ساخت/مدیریت اینستنس‌ها
==============================================
قواعد طلایی (تله شماره ۳ — امنیت Docker):
  ۱) فقط و فقط subprocess با لیست آرگومان — هرگز رشته‌ی فرمان (f-string shell) ممنوع.
  ۲) نام کانتینر فقط از order_id عددی ساخته می‌شود (بدون ورودی آزاد کاربر در مسیر/نام).
  ۳) توکن ربات قبل از دیپلوی با getMe اعتبارسنجی می‌شود؛ ورودی‌های کاربر هرگز وارد docker نمی‌شوند.
  ۴) مسیرهای حذف، با بررسی والد مسیر (guard) تأیید می‌شوند تا rm -rf اشتباه ممکن نباشد.

فرآیند provision (کاملاً خودکار، ~۱۰ ثانیه):
  دایرکتوری‌ها → دیتابیس sender_{id} → اسلات redis → .env (FERNET یکتا) → compose.yml → up -d → health-check
"""
import asyncio
import logging
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

import aiohttp
from cryptography.fernet import Fernet
from sqlalchemy import text

from config import config
from database import async_session
from database.engine import get_root_engine
from database.models import Order, RedisSlot
from templates.instance_env import render_instance_env
from templates.instance_compose import render_instance_compose

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
VOLUME_DIRS = ("sessions", "downloads", "exports", "banners", "profile_photos")

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")

def _safe_ident(name: str) -> str:
    """اعتبارسنجی شناسه MySQL قبل از قرارگیری در SQL (دفاع در عمق)."""
    if not _IDENT_RE.match(name):
        raise OrchestratorError(f"شناسه نامعتبر: {name!r}")
    return name


class OrchestratorError(Exception):
    """خطای عملیاتی با پیام فارسی قابل ارسال به ادمین."""

# نگهداری رفرنس تسک‌های پس‌زمینه برای جلوگیری از حذف توسط Garbage Collector (اصلاح M2)
_bg_tasks = set()

# قفل تخصیص اسلات redis — لازم برای صف دیپلوی چندکارگره (بدون race بین provision های موازی)
_slot_lock = asyncio.Lock()


# ============================================================
# ابزار امن اجرای فرمان
# ============================================================
async def run_cmd(args: list, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    """اجرای فرمان با لیست آرگومان (ناهمگام) — بدون shell= در هر صورت."""
    logger.debug("exec: %s", args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout = stdout_bytes.decode() if stdout_bytes else ""
        stderr = stderr_bytes.decode() if stderr_bytes else ""
    except FileNotFoundError:
        raise OrchestratorError(f"ابزار «{args[0]}» روی سرور نصب نیست.")
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        raise OrchestratorError(f"تایم‌اوت فرمان: {' '.join(args[:3])} ...")

    res = subprocess.CompletedProcess(
        args=args, returncode=proc.returncode, stdout=stdout, stderr=stderr
    )
    
    if check and res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip()[-400:]
        raise OrchestratorError(f"فرمان ناموفق ({res.returncode}): {err}")
    return res

def docker_cmd() -> list:
    return ["docker", "compose"]


# ============================================================
# اعتبارسنجی توکن ربات مشتری (قبل از هر چیزی)
# ============================================================
async def validate_bot_token(token: str) -> dict:
    """
    خروجی: {"ok": bool, "username": str, "title": str, "error": str}
    توکن نامعتبر/تلفرم نرسیده → ok=False با پیام فارسی مناسب مشتری.
    """
    token = token.strip()
    if not TOKEN_RE.match(token):
        return {"ok": False, "error": "فرمت توکن معتبر نیست. توکن باید شبیه «123456789:AAH...» باشد."}
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            # === در این خط، پراکسی V2rayN به درخواست اضافه شده است ===
            async with http.get(url, proxy=config.OUTBOUND_PROXY or None) as resp:
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.warning("getMe network error: %s", e)
        return {"ok": False, "error": "اتصال به تلگرام برقرار نشد؛ چند لحظه بعد دوباره امتحان کنید."}

    if not data.get("ok"):
        err = data.get("description", "unknown")
        if "Unauthorized" in err:
            return {"ok": False, "error": "توکن نامعتبر است — دوباره از BotFather کپی کنید."}
        return {"ok": False, "error": f"تلگرام توکن را نپذیرفت: {err}"}

    bot = data.get("result", {})
    return {
        "ok": True,
        "username": (bot.get("username") or "").lstrip("@"),
        "title": bot.get("first_name", ""),
    }


# ============================================================
# زیرساخت: دیتابیس + اسلات redis
# ============================================================
async def ensure_database(order_id: int, db_user: str, db_pass: str) -> None:
    """ساخت دیتابیس، ساخت کاربر اختصاصی با دسترسی کامل به همان DB، و اعطای دسترسی محدود به فکتوری."""
    order_id = int(order_id)
    db_name = _safe_ident(config.db_name_for(order_id))
    db_user = _safe_ident(db_user)
    engine = get_root_engine()
    
    # ساخت کاربر ایزوله برای اینستنس
    stmts = [
        f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
        f"CREATE USER IF NOT EXISTS '{db_user}'@'%' IDENTIFIED BY '{db_pass}'",
        f"ALTER USER '{db_user}'@'%' IDENTIFIED BY '{db_pass}'",
        f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'%'",
        # فکتوری برای سینک پروکسی فقط به عملیات پایه روی دیتابیس مشتری نیاز نیاز دارد
        f"GRANT SELECT, INSERT, UPDATE ON `{db_name}`.* TO '{config.FACTORY_APP_USER}'@'%'",
        "FLUSH PRIVILEGES"
    ]
    async with engine.connect() as conn:
        for stmt in stmts:
            await conn.execute(text(stmt))
    logger.info("🗄 دیتابیس %s آماده شد (کاربر %s ایجاد شد)", db_name, db_user)


async def drop_database(order_id: int, db_user: str = None) -> None:
    db_name = _safe_ident(config.db_name_for(order_id))
    if not db_user:
        db_user = f"sender_{int(order_id)}"
    db_user = _safe_ident(db_user)
        
    engine = get_root_engine()
    async with engine.connect() as conn:
        # ۱. ابتدا ابطال قطعی دسترسی‌ها
        try:
            await conn.execute(text(f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM '{db_user}'@'%'"))
        except Exception:
            pass
        # ۲. حذف کاربر قبل از حذف دیتابیس
        try:
            await conn.execute(text(f"DROP USER IF EXISTS '{db_user}'@'%'"))
        except Exception as e:
            logger.warning("خطا در حذف کاربر MySQL: %s", e)
        # ۳. در نهایت حذف دیتابیس
        await conn.execute(text(f"DROP DATABASE IF EXISTS `{db_name}`"))
    logger.info("🗄 دیتابیس %s و کاربر آن حذف شد", db_name)


async def alloc_redis_slot(order_id: int) -> int:
    """کمترین اسلات خالی را به سفارش می‌دهد.
    امن برای صف چندکارگره: قفل درون‌پردازشی + UPDATE اتمیک با گارد order_id IS NULL.
    در صورت وجود اسلات قبلی برای همین سفارش، آن را بازمی‌گرداند."""
    async with _slot_lock:
        async with async_session() as session:
            # اسلات قبلی همین سفارش را بازاستفاده کن (رفع بن‌بست re-provision پس از کرش میانی)
            row = (await session.execute(
                text("SELECT slot_id FROM redis_slots WHERE order_id = :oid"), {"oid": int(order_id)}
            )).first()
            if row:
                return int(row[0])

            row = (await session.execute(
                text("SELECT slot_id FROM redis_slots WHERE order_id IS NULL ORDER BY slot_id LIMIT 1")
            )).first()
            if row is None:
                raise OrchestratorError(
                    f"ظرفیت Redis پر است (همه {config.redis_capacity} اسلات تخصیص یافته). "
                    f"NUM_REDIS را در .env فکتوری افزایش دهید، کانتینر جدید را به docker-compose.yml اضافه کنید "
                    f"و docker compose up -d بزنید. (ظرفیت فعلی: {config.redis_capacity})"
                )
            slot = int(row[0])
            # اتمیک: فقط اگر همچنان آزاد بود (دفاع در برابر کارگر موازی)
            res = await session.execute(
                text("UPDATE redis_slots SET order_id = :oid WHERE slot_id = :sid AND order_id IS NULL"),
                {"oid": int(order_id), "sid": slot},
            )
            if res.rowcount == 0:
                raise OrchestratorError("تداخل در تخصیص اسلات redis؛ لطفاً دوباره تلاش کنید.")
            await session.commit()
            return slot


async def free_redis_slot(order_id: int) -> None:
    """آزادسازی اسلات ردیس و فلاش کامل آن دیتابیس منطقی به‌صورت امن."""
    async with async_session() as session:
        row = (await session.execute(
            text("SELECT slot_id FROM redis_slots WHERE order_id = :oid"),
            {"oid": int(order_id)}
        )).first()
        
        if row is not None:
            slot = int(row[0])
            redis_container, redis_db = config.redis_for_slot(slot)
            
            # فلاش ایمن دیتابیس مشخص شده (بدون تاثیر روی بقیه)
            try:
                await run_cmd([
                    "docker", "exec",
                    "-e", f"REDISCLI_AUTH={config.REDIS_PASS}",
                    redis_container,
                    "redis-cli", "-n", str(redis_db), "FLUSHDB"
                ], timeout=15)
            except Exception as e:
                logger.error("خطا در FlushDB ردیس %s: %s", redis_container, e)

        await session.execute(
            text("UPDATE redis_slots SET order_id = NULL WHERE order_id = :oid"),
            {"oid": int(order_id)},
        )
        await session.commit()


# ============================================================
# مسیرهای امن
# ============================================================
def instance_path(order_id: int) -> Path:
    """مسیر اینستنس با guard — نام فقط از عدد ساخته می‌شود."""
    order_id = int(order_id)
    inst = Path(config.instances_dir) / f"bot_{order_id}"
    if inst.parent != Path(config.instances_dir) or not inst.name.startswith("bot_"):
        raise OrchestratorError("مسیر اینستنس نامعتبر است (guard).")
    return inst


def compose_file(order_id: int) -> Path:
    return instance_path(order_id) / "compose.yml"


def compose_up_cmd(order_id: int, extra: list = None) -> list:
    args = docker_cmd() + ["-p", f"bot_{int(order_id)}", "-f", str(compose_file(order_id))]
    if extra:
        args += extra
    return args + ["up", "-d"]


# ============================================================
# PROVISION — ساخت کامل اینستنس
# ============================================================
async def provision(order: Order) -> dict:
    """دیپلوی ایزوله و محاسبه محدودیت کانکشن‌ها پیش از ساخت."""
    order_id = int(order.id)
    inst = instance_path(order_id)
    db_name = config.db_name_for(order_id)
    db_user = f"sender_{order_id}"
    slot = None

    try:
        # گارد محاسبات کانکشن (تله شماره ۳) — پول اتصال هر اینستنس از کانفیگ خوانده می‌شود
        pool_size = config.INSTANCE_DB_POOL_SIZE
        max_overflow = config.INSTANCE_DB_MAX_OVERFLOW
        # محاسبه فرمولی سقف ظرفیت (منبع واحد: config.max_instances_by_mysql)
        max_allowed_instances = config.max_instances_by_mysql
        
        async with async_session() as session:
            active_count = await session.scalar(
                text("SELECT COUNT(id) FROM orders WHERE status IN ('deployed', 'stopped', 'provisioning')")
            ) or 0
        
        if (active_count + 1) > max_allowed_instances:
            raise OrchestratorError(
                f"ظرفیت دیتابیس تکمیل است (سقف مجاز: {max_allowed_instances} اینستنس فعال، "
                f"فعلی: {active_count}). برای ظرفیت بیشتر MYSQL_MAX_CONNECTIONS را در .env افزایش دهید "
                f"(فعلی: {config.MYSQL_MAX_CONNECTIONS}؛ my.cnf هم باید همین عدد باشد) "
                f"یا INSTANCE_DB_POOL_SIZE / INSTANCE_DB_MAX_OVERFLOW را کم کنید."
            )

        # ۰) وضعیت جدید provisioning
        async with async_session() as session:
            await session.execute(text(
                "UPDATE orders SET status='provisioning' WHERE id=:oid"
            ), {"oid": order_id})
            await session.commit()

        # پاکسازی اینستنس قبلی با همین شماره (در حالت retry)
        if (inst / "compose.yml").exists():
            await run_cmd(docker_cmd() + ["-p", f"bot_{order_id}", "-f", str(inst / "compose.yml"), "down"],
                          timeout=60, check=False)

        # ۱) دایرکتوری‌ها + مالکیت 1000:1000 (بدون subprocess)
        def _create_instance_dirs():
            inst.mkdir(parents=True, exist_ok=True)
            for d in VOLUME_DIRS:
                d_path = inst / d
                d_path.mkdir(parents=True, exist_ok=True)
                if hasattr(os, 'chown'):
                    os.chown(d_path, 1000, 1000)

        await asyncio.to_thread(_create_instance_dirs)

        # ۲) دیتابیس اختصاصی
        db_pass = secrets.token_urlsafe(16)
        await ensure_database(order_id, db_user, db_pass)
        
        # ۳) اسلات و ACL ردیس
        slot = await alloc_redis_slot(order_id)
        redis_host, redis_db = config.redis_for_slot(slot)
        redis_user = f"bot_{order_id}"
        redis_pass = secrets.token_urlsafe(16)
        
        try:
            # تخصیص ACL: محدودیت دستورات مخرب (دفاع لایه‌ای)
            await run_cmd([
                "docker", "exec", 
                "-e", f"REDISCLI_AUTH={config.REDIS_PASS}",
                redis_host,
                "redis-cli", 
                "ACL", "SETUSER", redis_user, "on", f">{redis_pass}", "~*", "+@all", "-@dangerous", "-@admin"
            ], timeout=15)
        except Exception as e:
            logger.error("اخطار: تخصیص ACL ردیس برای %s ناموفق بود: %s", order_id, e)

        # ۳) کلید FERNET یکتا
        fernet_key = Fernet.generate_key().decode()

        # ۴) بهترین پروکسی فعلی به‌عنوان LOGIN_PROXY_URL اولیه
        login_proxy_url = ""
        try:
            from core.proxy_manager import get_best_proxy_url
            login_proxy_url = await get_best_proxy_url()
        except Exception as e:
            logger.warning("بدون پروکسی اولیه: %s", e)

        # ۵) فایل‌های کانفیگ
        env_content = render_instance_env(
            order_id=order_id,
            bot_token=order.bot_token,
            api_id=order.api_id,
            api_hash=order.api_hash,
            admin_id=order.admin_id or order.tg_id,
            db_name=db_name,
            db_user=db_user,
            db_pass=db_pass,
            redis_host=redis_host,
            redis_db=redis_db,
            redis_user=redis_user,
            redis_pass=redis_pass,
            fernet_key=fernet_key,
            force_join=order.force_join or "",
            login_proxy_url=login_proxy_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
        env_path = inst / ".env"
        with open(os.open(env_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), 'w', encoding="utf-8") as f:
            f.write(env_content)

        compose_path = inst / "compose.yml"
        compose_path.write_text(render_instance_compose(
            order_id=order_id,
            image=config.BOT_IMAGE,
            mem_limit=config.INSTANCE_MEM_LIMIT,
            cpus=config.INSTANCE_CPUS,
            pids_limit=config.INSTANCE_PIDS_LIMIT,
            log_size=config.INSTANCE_LOG_MAX_SIZE,
        ), encoding="utf-8")

        # ۶) روشن کردن
        await run_cmd(compose_up_cmd(order_id), timeout=180)

        # ۷) ثبت زیرساخت
        async with async_session() as session:
            await session.execute(text(
                "UPDATE orders SET redis_slot=:slot, fernet_key=:fk WHERE id=:oid"
            ), {"slot": slot, "fk": fernet_key, "oid": order_id})
            await session.commit()

        # ۸) بررسی استارتاپ
        started = await wait_for_startup(order_id, timeout=config.DEPLOY_STARTUP_TIMEOUT)
        if not started:
            res = await run_cmd(["docker", "logs", "--tail", "30", f"bot_{order_id}"], timeout=20, check=False)
            logs = (res.stdout or "") + (res.stderr or "")
            raise OrchestratorError(f"کانتینر روشن شد ولی مارکر استارتاپ دیده نشد.\nلاگ:\n{logs}")

        # ۹) نهایی‌سازی
        async with async_session() as session:
            await session.execute(text(
                "UPDATE orders SET status='deployed', "
                "deployed_at=NOW(), expires_at = DATE_ADD(NOW(), INTERVAL :days DAY), fail_reason='' "
                "WHERE id=:oid"
            ), {"days": int(order.duration_days or 30), "oid": order_id})
            await session.commit()

        from core.proxy_manager import sync_instance
        # 🎯 تخصیص خودکار پروکسی لاگین + سندر به اینستنس تازه‌ساخته (کارفرما:
        #    کاربر نهایی هیچ دخالتی ندارد — همه‌چیز از ربات‌ساز مدیریت می‌شود)
        try:
            from core.instance_proxies import auto_assign_for_new_instance
            assign_result = await auto_assign_for_new_instance(order_id)
            logger.info("🎯 تخصیص خودکار bot_%s: %s", order_id, assign_result)
        except Exception as e:
            logger.warning("تخصیص خودکار پروکسی bot_%s ناموفق: %s", order_id, e)

        task = asyncio.create_task(sync_instance(order_id, timeout=300))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)

        url = f"https://t.me/{order.bot_username}" if order.bot_username else ""
        return {"ok": True, "url": url, "container": f"bot_{order_id}", "error": ""}

    except (OrchestratorError, Exception) as e:
        err = str(e) if isinstance(e, OrchestratorError) else f"{type(e).__name__}: {e}"
        logger.exception("provision failed for order %s", order_id)
        # --- تغذیه معکوس کامل ---
        try:
            await run_cmd(docker_cmd() + ["-p", f"bot_{order_id}", "-f", str(inst / "compose.yml"), "down"],
                          timeout=60, check=False)
        except Exception:
            pass
        if slot is not None:
            try:
                await free_redis_slot(order_id)
            except Exception:
                pass
        try:
            await drop_database(order_id, db_user)
        except Exception:
            pass
        def _remove_instance_dir_safe():
            if inst.exists() and inst.is_dir():
                import shutil
                shutil.rmtree(inst)
        try:
            await asyncio.to_thread(_remove_instance_dir_safe)
        except Exception:
            pass

        async with async_session() as session:
            await session.execute(text(
                "UPDATE orders SET status='failed', fail_reason=:r WHERE id=:oid"
            ), {"r": err[:1000], "oid": order_id})
            await session.commit()
        return {"ok": False, "error": err, "url": "", "container": f"bot_{order_id}"}


# ============================================================
# چرخه حیات: stop / start / destroy / status
# ============================================================
async def stop(order_id: int) -> None:
    await run_cmd(docker_cmd() + ["-p", f"bot_{int(order_id)}", "-f", str(compose_file(order_id)), "stop"], timeout=90)


async def start(order_id: int) -> None:
    await run_cmd(compose_up_cmd(order_id), timeout=120)


async def destroy(order_id: int, drop_db: bool = True) -> dict:
    order_id = int(order_id)
    inst = instance_path(order_id)

    # ۱) خاموشی و حذف کانتینر
    await run_cmd(docker_cmd() + ["-p", f"bot_{order_id}", "-f", str(inst / "compose.yml"), "down"],
                  timeout=120, check=False)

    # ۲-الف) خواندن اسلات و حذف کاربر ACL ردیس پیش از آزادسازی اسلات
    try:
        async with async_session() as session:
            row = (await session.execute(
                text("SELECT redis_slot FROM orders WHERE id = :oid"),
                {"oid": order_id}
            )).first()
            
            if row is not None and row[0] is not None:
                slot = int(row[0])
                redis_container, _ = config.redis_for_slot(slot)
                redis_user = f"bot_{order_id}"
                
                await run_cmd([
                    "docker", "exec", 
                    "-e", f"REDISCLI_AUTH={config.REDIS_PASS}",
                    redis_container,
                    "redis-cli", "ACL", "DELUSER", redis_user
                ], timeout=15, check=False)
                logger.info("🗑 کاربر ACL ردیس %s حذف شد", redis_user)
    except Exception as e:
        logger.warning("خطا در حذف کاربر ACL ردیس سفارش %s: %s", order_id, e)

    # ۲-ب) آزادسازی اسلات (فلاش داده‌های ردیس انجام می‌شود)
    await free_redis_slot(order_id)

    # ۲-ج) 🎯 پاکسازی تخصیص‌های پروکسی این اینستنس (جدول instance_proxies)
    try:
        from core.instance_proxies import cleanup_instance
        await cleanup_instance(order_id)
    except Exception as e:
        logger.warning("پاکسازی تخصیص پروکسی bot_%s ناموفق: %s", order_id, e)

    # ۳) حذف دیتابیس مشتری و کاربر ایزوله
    if drop_db:
        db_user = f"sender_{order_id}"
        await drop_database(order_id, db_user)

    # ۴) حذف ایمن پوشه
    def _remove_instance_dir():
        if inst.exists() and inst.is_dir():
            import shutil
            shutil.rmtree(inst)

    await asyncio.to_thread(_remove_instance_dir)

    async with async_session() as session:
        await session.execute(text("UPDATE orders SET status='destroyed' WHERE id=:oid"),
                              {"oid": order_id})
        await session.commit()

    logger.info("💥 اینستنس bot_%s کاملاً حذف شد", order_id)
    return {"ok": True, "container": f"bot_{order_id}"}


async def status(order_id: int) -> dict:
    """وضعیت لحظه‌ای کانتینر (running/exited/missing + uptime)."""
    order_id = int(order_id)
    res = await run_cmd(
        ["docker", "inspect", "-f", "{{.State.Status}}|{{.State.StartedAt}}|{{.State.Running}}", f"bot_{order_id}"],
        timeout=20, check=False,
    )
    if res.returncode != 0:
        return {"exists": False, "state": "missing"}
    parts = (res.stdout or "").strip().split("|")
    return {
        "exists": True,
        "state": parts[0] if parts else "unknown",
        "running": parts[2] == "true" if len(parts) > 2 else False,
        "started_at": parts[1] if len(parts) > 1 else "",
    }


# ============================================================
# Health-check استارتاپ (اسکن لاگ)
# ============================================================
async def wait_for_startup(order_id: int, timeout: int = 120) -> bool:
    """منتظر ظهور مارکر «Master Control Panel» در لاگ کانتینر می‌ماند (ناهمگام بدون poll سنگین)."""
    deadline = asyncio.get_event_loop().time() + timeout
    start_time = int(time.time())
    
    while asyncio.get_event_loop().time() < deadline:
        res = await run_cmd(
            ["docker", "logs", "--since", str(start_time), f"bot_{int(order_id)}"], 
            timeout=20, check=False
        )
        out = (res.stdout or "") + (res.stderr or "")
        if config.INSTANCE_STARTUP_MARKER in out:
            logger.info("✅ bot_%s استارتاپ کامل شد", order_id)
            return True
        
        # کرش سریع
        st = await status(order_id)
        if st.get("exists") and not st.get("running"):
            logger.error("bot_%s بعد از استارت خاموش شد", order_id)
            return False
        
        await asyncio.sleep(5)
    return False


# ============================================================
# عملیات تعمیر و نگهداری (Maintenance)
# ============================================================
async def reconcile_redis_acls():
    """بازسازی ACL های ردیس پس از ری‌استارت ردیس سرور با خواندن از فایل‌های env."""
    async with async_session() as session:
        active_orders = (await session.execute(
            text("SELECT id, redis_slot FROM orders WHERE status IN ('deployed', 'stopped')")
        )).all()

    for row in active_orders:
        order_id, slot = row
        if slot is None: continue
        env_path = instance_path(order_id) / ".env"
        if not env_path.exists(): continue
        
        content = env_path.read_text(encoding="utf-8")
        redis_user_match = re.search(r"^REDIS_USER=(.+)$", content, re.M)
        redis_pass_match = re.search(r"^REDIS_PASS=(.+)$", content, re.M)
        
        if redis_user_match and redis_pass_match:
            r_user = redis_user_match.group(1).strip()
            r_pass = redis_pass_match.group(1).strip()
            redis_host, _ = config.redis_for_slot(slot)
            
            try:
                await run_cmd([
                    "docker", "exec", 
                    "-e", f"REDISCLI_AUTH={config.REDIS_PASS}",
                    redis_host,
                    "redis-cli", 
                    "ACL", "SETUSER", r_user, "on", f">{r_pass}", "~*", "+@all", "-@dangerous", "-@admin"
                ], timeout=15, check=False)
                logger.info("ACL ردیس برای %s بازیابی شد.", r_user)
            except Exception as e:
                logger.error("بازسازی ACL %s ناموفق بود: %s", r_user, e)