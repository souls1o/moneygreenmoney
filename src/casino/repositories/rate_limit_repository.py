from __future__ import annotations

from datetime import datetime

from pymongo import ReturnDocument

from casino.db.mongo import COLLECTION_RATE_LIMITS
from casino.repositories.base import MongoRepository


class RateLimitRepository(MongoRepository):
    def __init__(self, mongo) -> None:
        super().__init__(mongo, COLLECTION_RATE_LIMITS)

    async def increment(self, key: str, expires_at: datetime) -> int:
        document = await self.collection.find_one_and_update(
            {"key": key},
            {"$inc": {"count": 1}, "$setOnInsert": {"expiresAt": expires_at}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(document["count"])
