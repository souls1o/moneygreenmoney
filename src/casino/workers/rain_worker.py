from __future__ import annotations

from collections.abc import Awaitable, Callable

from casino.domain.records import RainRecord
from casino.logging.logger import get_logger
from casino.services.rain_service import RainService

RainListener = Callable[[RainRecord], Awaitable[None]]


class RainExpiryWorker:
    def __init__(self, rain_service: RainService) -> None:
        self._rains = rain_service
        self._logger = get_logger("casino.worker.rain")
        self._on_settled: RainListener | None = None

    def set_listener(self, listener: RainListener) -> None:
        self._on_settled = listener

    async def tick(self) -> None:
        settled = await self._rains.settle_due()
        if settled:
            self._logger.info("rains_settled", count=len(settled))
        if self._on_settled is None:
            return
        for rain in settled:
            try:
                await self._on_settled(rain)
            except Exception:
                self._logger.exception("rain_settle_listener_failed", rainId=rain.rain_id)
