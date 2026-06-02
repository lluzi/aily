import asyncio
import logging
from typing import Callable, Awaitable

from aily.queue.db import QueueDB

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(
        self,
        db: QueueDB,
        processor: Callable[[dict], Awaitable[None]],
        poll_interval: float = 2.0,
        stale_running_seconds: float = 1800.0,
        max_retries: int = 3,
    ) -> None:
        self.db = db
        self.processor = processor
        self.poll_interval = poll_interval
        self.stale_running_seconds = max(60.0, float(stale_running_seconds))
        self.max_retries = max(1, int(max_retries))
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                recovered = await self.db.requeue_stale_running_jobs(stale_after_seconds=self.stale_running_seconds)
                if recovered:
                    logger.warning("Recovered %d stale primary queue jobs", recovered)
                job = await self.db.dequeue()
            except Exception:
                logger.exception("Dequeue failed")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.poll_interval
                    )
                except asyncio.TimeoutError:
                    continue
                return
            if job is None:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.poll_interval
                    )
                except asyncio.TimeoutError:
                    continue
                return
            try:
                await self.processor(job)
                await self.db.complete_job(job["id"], success=True)
            except Exception as exc:
                logger.exception("Job %s failed", job["id"])
                will_retry = await self.db.retry_job(
                    job["id"],
                    max_retries=self.max_retries,
                    error_message=str(exc),
                )
                if not will_retry:
                    await self.db.complete_job(job["id"], success=False, error_message=str(exc))
