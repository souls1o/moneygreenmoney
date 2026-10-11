from __future__ import annotations

from datetime import timedelta

from casino.config.settings import Settings
from casino.errors import RateLimitExceededError
from casino.repositories.rate_limit_repository import RateLimitRepository
from casino.utils.clock import Clock


class RateLimitService:
    def __init__(
        self,
        repository: RateLimitRepository,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._limit = settings.rate_limit_commands_per_window
        self._window = settings.rate_limit_window_seconds

    async def enforce(self, key: str) -> None:
        now = self._clock.now()
        expires_at = now + timedelta(seconds=self._window)
        count = await self._repository.increment(f"{key}:{int(now.timestamp() // self._window)}", expires_at)
        if count > self._limit:
            raise RateLimitExceededError()
