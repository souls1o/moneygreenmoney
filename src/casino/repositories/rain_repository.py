from __future__ import annotations

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClientSession
from pymongo import ReturnDocument

from casino.db.mongo import COLLECTION_RAINS
from casino.domain.enums import RainStatus
from casino.domain.records import RainRecord
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper


class RainRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_RAINS)
        self._mapper = mapper

    async def insert(self, rain: RainRecord, session: AsyncIOMotorClientSession | None = None) -> None:
        await self.collection.insert_one(self._mapper.rain_to_document(rain), session=session)

    async def find_by_id(
        self, rain_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> RainRecord | None:
        document = await self.collection.find_one({"rainId": rain_id}, session=session)
        return self._mapper.rain_from_document(document) if document else None

    async def replace(self, rain: RainRecord, session: AsyncIOMotorClientSession | None = None) -> bool:
        result = await self.collection.replace_one(
            {"rainId": rain.rain_id, "version": rain.version},
            {**self._mapper.rain_to_document(rain), "version": rain.version + 1},
            session=session,
        )
        if result.matched_count == 1:
            rain.version += 1
            return True
        return False

    async def set_listing_ref(
        self,
        rain_id: str,
        channel_id: str,
        message_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"rainId": rain_id},
            {"$set": {"channelId": channel_id, "messageId": message_id}},
            session=session,
        )

    async def try_add_joiner(
        self,
        rain_id: str,
        player_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> RainRecord | None:
        document = await self.collection.find_one_and_update(
            {
                "rainId": rain_id,
                "status": RainStatus.PENDING.value,
                "creatorId": {"$ne": player_id},
                "joinerIds": {"$nin": [player_id]},
                "expiresAt": {"$gt": now},
            },
            {
                "$addToSet": {"joinerIds": player_id},
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.rain_from_document(document) if document else None

    async def try_begin_settle(
        self,
        rain_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> RainRecord | None:
        document = await self.collection.find_one_and_update(
            {
                "rainId": rain_id,
                "status": RainStatus.PENDING.value,
                "expiresAt": {"$lte": now},
            },
            {
                "$set": {"status": RainStatus.SETTLING.value},
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.rain_from_document(document) if document else None

    async def list_due(
        self, now: datetime, limit: int = 50, session: AsyncIOMotorClientSession | None = None
    ) -> list[RainRecord]:
        cursor = self.collection.find(
            {
                "status": {"$in": [RainStatus.PENDING.value, RainStatus.SETTLING.value]},
                "expiresAt": {"$lte": now},
            }
        ).limit(limit)
        return [self._mapper.rain_from_document(document) async for document in cursor]

    async def list_open(self, session: AsyncIOMotorClientSession | None = None) -> list[RainRecord]:
        cursor = self.collection.find({"status": RainStatus.PENDING.value})
        return [self._mapper.rain_from_document(document) async for document in cursor]
