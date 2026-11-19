from __future__ import annotations

from collections.abc import Awaitable, Callable

from casino.domain.records import JackpotRecord
from casino.logging.logger import get_logger
from casino.services.jackpot_service import JackpotService

JackpotListener = Callable[[JackpotRecord], Awaitable[None]]


class JackpotWorker:
    def __init__(self, jackpot_service: JackpotService) -> None:
        self._jackpots = jackpot_service
        self._logger = get_logger("casino.worker.jackpot")
        self._on_settled: JackpotListener | None = None
        self._on_reset: JackpotListener | None = None

    def set_listeners(self, *, on_settled=None, on_reset=None) -> None:
        self._on_settled = on_settled
        self._on_reset = on_reset

    async def tick(self) -> None:
        settled = await self._jackpots.settle_due()
        if settled:
            self._logger.info("jackpots_settled", count=len(settled))
        if self._on_settled is not None:
            for jackpot in settled:
                try:
                    await self._on_settled(jackpot)
                except Exception:
                    self._logger.exception("jackpot_settle_listener_failed", jackpotId=jackpot.jackpot_id)
        opened = await self._jackpots.reset_due()
        if self._on_reset is not None:
            for jackpot in opened:
                try:
                    await self._on_reset(jackpot)
                except Exception:
                    self._logger.exception("jackpot_reset_listener_failed", jackpotId=jackpot.jackpot_id)
