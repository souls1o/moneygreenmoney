from __future__ import annotations

from collections.abc import Awaitable, Callable

from casino.domain.records import GameRecord
from casino.logging.logger import get_logger
from casino.services.game_service import GameService

ExpiryListener = Callable[[GameRecord], Awaitable[None]]


class GameExpiryWorker:
    def __init__(self, game_service: GameService) -> None:
        self._games = game_service
        self._logger = get_logger("casino.worker.expiry")
        self._on_expired: ExpiryListener | None = None
        self._on_channel_idle: ExpiryListener | None = None

    def set_listener(self, listener: ExpiryListener) -> None:
        self._on_expired = listener

    def set_channel_listener(self, listener: ExpiryListener) -> None:
        self._on_channel_idle = listener

    async def tick(self) -> None:
        flipped = await self._games.auto_flip_due_games()
        if flipped:
            self._logger.info("games_auto_flipped", count=len(flipped))
        if self._on_expired is not None:
            for game in flipped:
                try:
                    await self._on_expired(game)
                except Exception:
                    self._logger.exception("auto_flip_listener_failed", gameId=game.game_id)
        closed = await self._games.close_idle_tables()
        if closed:
            self._logger.info("idle_tables_closed", count=len(closed))
        if self._on_channel_idle is None:
            return
        for game in closed:
            try:
                await self._on_channel_idle(game)
            except Exception:
                self._logger.exception("idle_channel_listener_failed", gameId=game.game_id)
