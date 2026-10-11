"""任务队列抽象：生产走 arq（Redis），本地测试走进程内执行。

Admin API 与调度器只依赖 BaseQueue 接口；两种实现的差别（持久化、
跨进程分发）对调用方透明。
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


async def enqueue_review_jobs(redis, content_ids: list[int]) -> str | None:
    """同一内容排队/执行期间只保留一份审核，完成后允许新版本入队。"""
    first_job_id = None
    for content_id in dict.fromkeys(content_ids):
        job = await redis.enqueue_job("review_contents", [content_id], _job_id=f"review-content:{content_id}")
        if job is not None and first_job_id is None:
            first_job_id = getattr(job, "job_id", None)
    return first_job_id


class BaseQueue:
    async def enqueue_collect(self, target_id: int) -> str | None:
        raise NotImplementedError

    async def pending_count(self) -> int | None:
        raise NotImplementedError

    async def enqueue_notification(self, delivery_id: int) -> str | None:
        raise NotImplementedError

    async def enqueue_morning(self) -> str | None:
        raise NotImplementedError

    async def enqueue_review(self, content_ids: list[int]) -> str | None:
        raise NotImplementedError

    async def enqueue_admin_email(self, delivery_ids: list[int]) -> str | None:
        raise NotImplementedError

    async def enqueue_custom_sweep(self) -> str | None:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


class LocalRedis:
    """platform_slot 依赖的最小内存实现（SET NX PX / PTTL 语义）。"""

    def __init__(self) -> None:
        self._keys: dict[str, float] = {}

    async def set(self, key: str, value: str, nx: bool = False, px: int | None = None) -> bool | None:
        now = time.monotonic()
        if nx:
            expiry = self._keys.get(key, 0.0)
            if expiry > now:
                return None
        self._keys[key] = now + (px / 1000.0 if px else float("inf"))
        return True

    async def pttl(self, key: str) -> int:
        expiry = self._keys.get(key)
        if expiry is None:
            return -2
        remaining_ms = int((expiry - time.monotonic()) * 1000)
        return max(0, remaining_ms)


class LocalQueue(BaseQueue):
    """本地测试队列：采集任务作为当前进程的 asyncio task 执行。

    共享一个 LocalRedis，保证同平台礼貌间隔在并发 task 之间同样生效。
    """

    def __init__(self) -> None:
        self._redis = LocalRedis()
        self._tasks: set[asyncio.Task] = set()

    @property
    def redis(self) -> LocalRedis:
        return self._redis

    async def enqueue_collect(self, target_id: int) -> str | None:
        from app.services.collect import run_collection

        task = asyncio.create_task(self._run(run_collection, target_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return f"local:{target_id}:{time.time():.0f}"

    async def enqueue_notification(self, delivery_id: int) -> str | None:
        from app.db import SessionLocal
        from app.services.notifications import process_delivery

        async def run() -> None:
            async with SessionLocal() as session:
                await process_delivery(session, delivery_id)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return f"local:notification:{delivery_id}:{time.time():.0f}"

    async def enqueue_morning(self) -> str | None:
        from app.db import SessionLocal
        from app.services.notifications import run_scheduled_morning

        async def run() -> None:
            async with SessionLocal() as session:
                await run_scheduled_morning(session)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return f"local:morning:{time.time():.0f}"

    async def enqueue_review(self, content_ids: list[int]) -> str | None:
        from app.db import SessionLocal
        from app.services.curation import review_contents

        async def run() -> None:
            async with SessionLocal() as session:
                await review_contents(session, content_ids)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return f"local:review:{content_ids[0] if content_ids else 0}:{time.time():.0f}"

    async def enqueue_admin_email(self, delivery_ids: list[int]) -> str | None:
        from app.db import SessionLocal
        from app.services.notifications import send_admin_email

        async def run() -> None:
            async with SessionLocal() as session:
                for delivery_id in delivery_ids:
                    await send_admin_email(session, delivery_id)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return f"local:admin-email:{delivery_ids[0] if delivery_ids else 0}:{time.time():.0f}"

    async def enqueue_custom_sweep(self) -> str | None:
        from app.db import SessionLocal
        from app.services.custom_feeds import sweep_custom_feeds

        async def run() -> None:
            async with SessionLocal() as session:
                await sweep_custom_feeds(session, self._redis)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return f"local:custom-sweep:{time.time():.0f}"

    async def _run(self, run_collection, target_id: int) -> None:
        try:
            await run_collection(self._redis, target_id)
        except Exception:  # noqa: BLE001 - 与 arq worker 行为一致：单任务失败不终止
            logger.exception("local collect task %s failed", target_id)

    async def pending_count(self) -> int | None:
        return len(self._tasks)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()


class ArqQueue(BaseQueue):
    """生产队列：包装 arq Redis 池。"""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def enqueue_collect(self, target_id: int) -> str | None:
        job = await self._pool.enqueue_job("collect_target", target_id)
        return getattr(job, "job_id", None) if job is not None else None

    async def pending_count(self) -> int | None:
        try:
            return await self._pool.zcard("arq:queue")
        except Exception as exc:  # noqa: BLE001 - 队列深度是尽力而为的观测值
            logger.warning("queue depth unavailable: %r", exc)
            return None

    async def enqueue_notification(self, delivery_id: int) -> str | None:
        job = await self._pool.enqueue_job("send_user_notification", delivery_id)
        return getattr(job, "job_id", None) if job is not None else None

    async def enqueue_morning(self) -> str | None:
        job = await self._pool.enqueue_job("send_morning_notifications")
        return getattr(job, "job_id", None) if job is not None else None

    async def enqueue_review(self, content_ids: list[int]) -> str | None:
        return await enqueue_review_jobs(self._pool, content_ids)

    async def enqueue_admin_email(self, delivery_ids: list[int]) -> str | None:
        job = await self._pool.enqueue_job("send_admin_emails", delivery_ids)
        return getattr(job, "job_id", None) if job is not None else None

    async def enqueue_custom_sweep(self) -> str | None:
        job = await self._pool.enqueue_job("collect_custom_feeds")
        return getattr(job, "job_id", None) if job is not None else None

    async def aclose(self) -> None:
        await self._pool.aclose()
