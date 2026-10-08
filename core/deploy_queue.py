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
from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass
class DeployJob:
    action: str                      # provision | destroy | stop | start
    order_id: int
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
            async with async_session() as session:
                await session.execute(text("UPDATE orders SET status='stopping' WHERE id=:o"), {"o": job.order_id})
                await session.commit()
            try:
                await orchestrator.stop(job.order_id)
            except Exception as e:
                logger.error("خطا در توقف bot_%s: %s", job.order_id, e)
            
            st = await orchestrator.status(job.order_id)
            real_status = 'stopped' if not st.get("running") else 'deployed'
            async with async_session() as session:
                await session.execute(text("UPDATE orders SET status=:s WHERE id=:o"),
                                      {"s": real_status, "o": job.order_id})
                await session.commit()
            return {"ok": real_status == 'stopped'}

        if job.action == "start":
            async with async_session() as session:
                await session.execute(text("UPDATE orders SET status='starting' WHERE id=:o"), {"o": job.order_id})
                await session.commit()
            try:
                await orchestrator.start(job.order_id)
            except Exception as e:
                logger.error("خطا در استارت bot_%s: %s", job.order_id, e)
                
            st = await orchestrator.status(job.order_id)
            real_status = 'deployed' if st.get("running") else 'stopped'
            async with async_session() as session:
                await session.execute(text("UPDATE orders SET status=:s WHERE id=:o"),
                                      {"s": real_status, "o": job.order_id})
                await session.commit()
            return {"ok": real_status == 'deployed'}

        return {"ok": False, "error": f"action نامعتبر: {job.action}"}


# نمونه سراسری — تعداد کارگر و ظرفیت صف از .env (آماده موج ۵۰ سفارش همزمان)
deploy_queue = DeployQueue(
    num_workers=config.DEPLOY_QUEUE_WORKERS,
    max_size=config.DEPLOY_QUEUE_SIZE,
)