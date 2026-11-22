from __future__ import annotations

from collections.abc import Awaitable, Callable

from casino.domain.records import BaccaratRecord
from casino.logging.logger import get_logger
from casino.services.baccarat_service import BaccaratService

BaccaratListener = Callable[[BaccaratRecord], Awaitable[None]]


class BaccaratWorker:
    def __init__(self, baccarat_service: BaccaratService) -> None:
        self._tables = baccarat_service
        self._logger = get_logger("casino.worker.baccarat")
        self._on_settled: BaccaratListener | None = None
        self._on_reset: BaccaratListener | None = None

    def set_listeners(self, *, on_settled=None, on_reset=None) -> None:
        self._on_settled = on_settled
        self._on_reset = on_reset

    async def tick(self) -> None:
        settled = await self._tables.settle_due()
        if settled:
            self._logger.info("baccarat_settled", count=len(settled))
        if self._on_settled is not None:
            for table in settled:
                try:
                    await self._on_settled(table)
                except Exception:
                    self._logger.exception("baccarat_settle_listener_failed", baccaratId=table.baccarat_id)
        opened = await self._tables.reset_due()
        if self._on_reset is not None:
            for table in opened:
                try:
                    await self._on_reset(table)
                except Exception:
                    self._logger.exception("baccarat_reset_listener_failed", baccaratId=table.baccarat_id)
