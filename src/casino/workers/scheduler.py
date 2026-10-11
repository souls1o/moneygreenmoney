from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from casino.logging.logger import get_logger
from casino.workers.locks import DistributedLockService


class WorkerScheduler:
    def __init__(self, lock_service: DistributedLockService) -> None:
        self._lock_service = lock_service
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()
        self._logger = get_logger("casino.workers")

    def start(self, name: str, interval_seconds: int, callback: Callable[[], Awaitable[None]]) -> None:
        self._tasks.append(asyncio.create_task(self._run(name, interval_seconds, callback), name=name))

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._logger.info("workers_stopped")

    async def _run(
        self,
        name: str,
        interval_seconds: int,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        while not self._stopping.is_set():
            try:
                acquired = await self._lock_service.acquire(name, ttl_seconds=interval_seconds * 3)
                if acquired:
                    try:
                        await callback()
                    finally:
                        await self._lock_service.release(name)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("worker_tick_failed", worker=name)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
