"""
صف دیپلوی — قلب آرامش CPU
==========================
عملیات سنگین (provision/destroy) از این صف عبور می‌کنند تا:
  • CPU سرور در لحظه سفارش‌های همزمان اسپایک نکند
  • اسلات‌های redis بدون race تخصیص یابند (قفل درون‌پردازشی + UPDATE اتمیک در orchestrator)
  • در صورت شکست، سفارش بعدی قفل نشود

تعداد کارگرها و ظرفیت صف از .env خوانده می‌شود (DEPLOY_QUEUE_WORKERS / DEPLOY_QUEUE_SIZE)
تا موج سفارش‌های همزمان (مثلاً ۵۰ سفارش) بدون گم‌شدن هضم شود.

هر Job پس از پایان، callback روی‌آمده (notify) را صدا می‌زند تا ادمین/مشتری پیام بگیرند.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

from config import config
from core import orchestrator
from database import async_session
from database.models import Order
from sqlalchemy import text, select

logger = logging.getLogger(__name__)


@dataclass
class DeployJob:
    action: str                      # provision | destroy | stop | start | redeploy | rollout_all
    order_id: int                    # برای rollout_all مقدار 0 است (همه اینستنس‌ها)
    chat_id: Optional[int] = None    # چت مشتری برای اطلاع نتیجه
    done_cb: Optional[Callable[[dict], Awaitable[None]]] = None
    context: dict = field(default_factory=dict)


class DeployQueue:
    def __init__(self, num_workers: int = 1, max_size: int = 20):
        self._q: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._workers: list = []
        self.num_workers = num_workers
        self.max_size = max_size
        self.busy_workers = 0          # تعداد کارگرهای مشغول (چندکارگره)
        self.stats = {"processed": 0, "failed": 0}

    @property
    def busy(self) -> bool:
        """سازگاری با کدهای قبلی — True اگر حداقل یک کارگر مشغول باشد."""
        return self.busy_workers > 0

    def submit(self, job: DeployJob) -> None:
        try:
            self._q.put_nowait(job)
            logger.info("📥 job در صف: %s#%s (طول صف: %s)", job.action, job.order_id, self._q.qsize())
        except asyncio.QueueFull:
            logger.error("ظرفیت صف سیستم پر است! رد job %s#%s", job.action, job.order_id)
            if job.done_cb:
                asyncio.create_task(job.done_cb({"ok": False, "error": "ظرفیت صف سیستم پر است. لطفاً چند دقیقه دیگر تلاش کنید."}))

    async def start(self) -> None:
        for i in range(self.num_workers):
            t = asyncio.create_task(self._worker(i), name=f"deploy-worker-{i}")
            self._workers.append(t)

    async def stop(self) -> None:
        for t in self._workers:
            t.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    async def _worker(self, wid: int) -> None:
        logger.info("🛠 deploy-worker-%s روشن شد", wid)
        while True:
            job = await self._q.get()
            self.busy_workers += 1
            try:
                result = await self._execute(job)
                self.stats["processed"] += 1
                if job.done_cb:
                    try:
                        await job.done_cb(result)
                    except Exception:
                        logger.exception("callback نتیجه job خطا داد")
            except Exception as e:
                self.stats["failed"] += 1
                logger.exception("job %s#%s کرش کرد", job.action, job.order_id)
                if job.done_cb:
                    try:
                        await job.done_cb({"ok": False, "error": f"{type(e).__name__}: {e}"})
                    except Exception:
                        pass
            finally:
                self.busy_workers -= 1
                self._q.task_done()

    async def _execute(self, job: DeployJob) -> dict:
        if job.action == "provision":
            async with async_session() as session:
                order = await session.get(Order, job.order_id)
                if order is None:
                    return {"ok": False, "error": "سفارش یافت نشد."}
                return await orchestrator.provision(order)

        if job.action == "destroy":
            return await orchestrator.destroy(job.order_id)

        if job.action == "stop":
            # وضعیت قبلی را نگه می‌داریم تا سفارش failed در اثر توقف به stopped تبدیل نشود
            async with async_session() as session:
                prior_status = await session.scalar(
                    select(Order.status).where(Order.id == job.order_id))
                await session.execute(text("UPDATE orders SET status='stopping' WHERE id=:o"), {"o": job.order_id})
                await session.commit()
            stop_ok = True
            try:
                await orchestrator.stop(job.order_id)
            except Exception as e:
                stop_ok = False
                logger.error("خطا در توقف bot_%s: %s", job.order_id, e)

            # 🩹 FIX (S3): status خودش روی تایم‌اوت/خطای داکر Exception می‌دهد —
            # قبلاً این خط می‌توانست کل جاب را بپراند و وضعیت سفارش برای همیشه
            # «stopping» بماند (خروج از لیست اینستنس‌ها). حالا محافظت‌شده است.
            st = None
            try:
                st = await orchestrator.status(job.order_id)
            except Exception as e:
                logger.warning("بررسی وضعیت پس از توقف bot_%s ناموفق: %s", job.order_id, e)

            if st is not None:
                real_status = 'stopped' if not st.get("running") else 'deployed'
            elif stop_ok:
                # خود دستور stop موفق بود ولی inspect جواب نداد — متوقف فرض می‌کنیم
                real_status = 'stopped'
            else:
                # نه stop موفق بود نه inspect — کانتینر به احتمال زیاد هنوز روشن است
                real_status = 'deployed'

            # 🩹 FIX (S3): سفارش failed (بدون compose) با توقف نباید «stopped» شود
            if real_status == 'stopped' and prior_status == 'failed':
                real_status = 'failed'

            async with async_session() as session:
                await session.execute(text("UPDATE orders SET status=:s WHERE id=:o"),
                                      {"s": real_status, "o": job.order_id})
                await session.commit()
            return {"ok": real_status == 'stopped', "status": real_status}

        if job.action == "start":
            async with async_session() as session:
                prior_status = await session.scalar(
                    select(Order.status).where(Order.id == job.order_id))
                await session.execute(text("UPDATE orders SET status='starting' WHERE id=:o"), {"o": job.order_id})
                await session.commit()
            start_ok = True
            try:
                await orchestrator.start(job.order_id)
            except Exception as e:
                start_ok = False
                logger.error("خطا در استارت bot_%s: %s", job.order_id, e)

            # 🩹 FIX (S3): همان محافظت برای استارت — خطای گذرای inspect دیگر
            # وضعیت را قفل نمی‌کند و سفارش failed واقعی می‌ماند
            st = None
            try:
                st = await orchestrator.status(job.order_id)
            except Exception as e:
                logger.warning("بررسی وضعیت پس از استارت bot_%s ناموفق: %s", job.order_id, e)

            if st is not None:
                real_status = 'deployed' if st.get("running") else 'stopped'
            elif start_ok:
                real_status = 'deployed'
            else:
                real_status = 'stopped'

            # سفارش failed بدون compose واقعاً راه‌اندازی نمی‌شود — failed بماند
            if real_status == 'stopped' and prior_status == 'failed':
                real_status = 'failed'

            async with async_session() as session:
                await session.execute(text("UPDATE orders SET status=:s WHERE id=:o"),
                                      {"s": real_status, "o": job.order_id})
                await session.commit()
            return {"ok": real_status == 'deployed', "status": real_status}

        # 🩹 FIX (رُل‌اوت): اعمال ایمیج جدید روی یک اینستنس موجود — قبلاً هیچ
        # مسیری (نه پنل، نه صف) کانتینر در حال اجرا را Recreate نمی‌کرد و تنها
        # راه، حلقه دستی force-recreate در README بود.
        if job.action == "redeploy":
            result = await orchestrator.redeploy(job.order_id)
            # همگام‌سازی وضعیت سفارش با واقعیت کانتینر (اگر recreate شکست خورد
            # و کانتینر خاموش ماند، ادمین بتواند از پنل start بزند)
            real_status = 'deployed'
            try:
                st = await orchestrator.status(job.order_id)
                real_status = 'deployed' if st.get("running") else 'stopped'
                async with async_session() as session:
                    await session.execute(
                        text("UPDATE orders SET status=:s WHERE id=:o AND status IN ('deployed','stopped')"),
                        {"s": real_status, "o": job.order_id})
                    await session.commit()
            except Exception:
                logger.exception("به‌روزرسانی وضعیت سفارش پس از redeploy ناموفق")
            if result.get("ok") and real_status != 'deployed':
                result = {"ok": False, "container": result.get("container", f"bot_{job.order_id}"),
                          "error": "کانتینر بعد از بازسازی در حال اجرا نیست."}
            return result

        # 🚀 رُل‌اوت ایمیج جدید روی همه اینستنس‌های deployed (سریال، از همین صف —
        # تا با provision های موازی رقابت نکند و CPU/دیتابیس اسپایک نزند)
        if job.action == "rollout_all":
            return await orchestrator.rollout_all()

        return {"ok": False, "error": f"action نامعتبر: {job.action}"}


# نمونه سراسری — تعداد کارگر و ظرفیت صف از .env (آماده موج ۵۰ سفارش همزمان)
deploy_queue = DeployQueue(
    num_workers=config.DEPLOY_QUEUE_WORKERS,
    max_size=config.DEPLOY_QUEUE_SIZE,
)