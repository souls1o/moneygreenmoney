from __future__ import annotations

from datetime import timedelta

from casino.repositories.lock_repository import LockRepository
from casino.utils.clock import Clock
from casino.utils.identifiers import IdentifierFactory


class DistributedLockService:
    def __init__(
        self,
        repository: LockRepository,
        clock: Clock,
        identifiers: IdentifierFactory,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers
        self.owner = identifiers.lock_owner()

    async def acquire(self, name: str, ttl_seconds: int) -> bool:
        expires_at = self._clock.now() + timedelta(seconds=ttl_seconds)
        return await self._repository.acquire(name, self.owner, expires_at)

    async def release(self, name: str) -> None:
        await self._repository.release(name, self.owner)
