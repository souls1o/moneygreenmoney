from __future__ import annotations

from collections.abc import Awaitable, Callable

from casino.domain.records import WagerRaceRecord
from casino.logging.logger import get_logger
from casino.services.wager_race_service import WagerRaceService

WagerRaceListener = Callable[[WagerRaceRecord], Awaitable[None]]


class WagerRaceWorker:
    def __init__(self, races: WagerRaceService) -> None:
        self._races = races
        self._logger = get_logger("casino.worker.wager_race")
        self._on_settled: WagerRaceListener | None = None
        self._on_open: WagerRaceListener | None = None

    def set_listeners(self, *, on_settled=None, on_open=None) -> None:
        self._on_settled = on_settled
        self._on_open = on_open

    async def tick(self) -> None:
        settled = await self._races.settle_due()
        if settled:
            self._logger.info("wager_races_settled", count=len(settled))
        if self._on_settled is not None:
            for race in settled:
                try:
                    await self._on_settled(race)
                except Exception:
                    self._logger.exception("wager_race_settle_listener_failed", raceId=race.race_id)
        opened = await self._races.ensure_configured()
        if self._on_open is not None:
            for race in opened:
                try:
                    await self._on_open(race)
                except Exception:
                    self._logger.exception("wager_race_open_listener_failed", raceId=race.race_id)
