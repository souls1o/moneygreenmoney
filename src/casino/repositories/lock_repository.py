from __future__ import annotations

from datetime import UTC, datetime

from pymongo.errors import DuplicateKeyError

from casino.db.mongo import COLLECTION_LOCKS
from casino.repositories.base import MongoRepository
from casino.utils.clock import DateTimeNormalizer


class LockRepository(MongoRepository):
    def __init__(self, mongo, normalizer: DateTimeNormalizer | None = None) -> None:
        super().__init__(mongo, COLLECTION_LOCKS)
        self._time = normalizer or DateTimeNormalizer()

    async def acquire(self, name: str, owner: str, expires_at: datetime) -> bool:
        existing = await self.collection.find_one({"name": name})
        if existing is None:
            try:
                await self.collection.insert_one(
                    {"name": name, "owner": owner, "expiresAt": expires_at}
                )
                return True
            except DuplicateKeyError:
                return False
        current_expiry = self._time.to_utc(existing.get("expiresAt"))
        now = datetime.now(UTC)
        if current_expiry and now and current_expiry <= now:
            result = await self.collection.update_one(
                {"name": name, "expiresAt": existing["expiresAt"]},
                {"$set": {"owner": owner, "expiresAt": expires_at}},
            )
            return result.modified_count == 1
        return False

    async def release(self, name: str, owner: str) -> None:
        await self.collection.delete_one({"name": name, "owner": owner})
