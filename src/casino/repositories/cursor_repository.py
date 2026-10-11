from __future__ import annotations

from datetime import datetime

from casino.db.mongo import COLLECTION_CURSORS
from casino.domain.records import CursorRecord
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper


class CursorRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_CURSORS)
        self._mapper = mapper

    async def get(self, name: str) -> CursorRecord | None:
        document = await self.collection.find_one({"name": name})
        return self._mapper.cursor_from_document(document) if document else None

    async def set(self, name: str, value: str, now: datetime) -> None:
        await self.collection.update_one(
            {"name": name},
            {"$set": {"name": name, "value": value, "updatedAt": now}},
            upsert=True,
        )
