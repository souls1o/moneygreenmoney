from __future__ import annotations

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClientSession
from pymongo import ReturnDocument

from casino.db.mongo import COLLECTION_BACCARAT
from casino.domain.enums import BaccaratStatus
from casino.domain.records import BaccaratRecord
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper


_LIVE = [
    BaccaratStatus.OPEN.value,
    BaccaratStatus.SHOWING.value,
    BaccaratStatus.SETTLING.value,
]


class BaccaratRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_BACCARAT)
        self._mapper = mapper

    async def insert(self, table: BaccaratRecord, session: AsyncIOMotorClientSession | None = None) -> None:
        await self.collection.insert_one(self._mapper.baccarat_to_document(table), session=session)

    async def find_by_id(
        self, baccarat_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> BaccaratRecord | None:
        document = await self.collection.find_one({"baccaratId": baccarat_id}, session=session)
        return self._mapper.baccarat_from_document(document) if document else None

    async def find_live(
        self, guild_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> BaccaratRecord | None:
        document = await self.collection.find_one(
            {"guildId": guild_id, "status": {"$in": _LIVE}},
            session=session,
        )
        return self._mapper.baccarat_from_document(document) if document else None

    async def replace(
        self, table: BaccaratRecord, session: AsyncIOMotorClientSession | None = None
    ) -> bool:
        result = await self.collection.replace_one(
            {"baccaratId": table.baccarat_id, "version": table.version},
            {**self._mapper.baccarat_to_document(table), "version": table.version + 1},
            session=session,
        )
        if result.matched_count == 1:
            table.version += 1
            return True
        return False

    async def set_listing_ref(
        self,
        baccarat_id: str,
        channel_id: str,
        message_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"baccaratId": baccarat_id},
            {"$set": {"channelId": channel_id, "messageId": message_id}},
            session=session,
        )

    async def set_history_ref(
        self,
        baccarat_id: str,
        message_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"baccaratId": baccarat_id},
            {"$set": {"historyMessageId": message_id}},
            session=session,
        )

    async def list_due(self, now: datetime) -> list[BaccaratRecord]:
        cursor = self.collection.find(
            {
                "status": BaccaratStatus.OPEN.value,
                "openerId": {"$ne": None},
                "expiresAt": {"$lte": now},
            }
        )
        return [self._mapper.baccarat_from_document(document) async for document in cursor]

    async def list_reveal_due(self, now: datetime) -> list[BaccaratRecord]:
        cursor = self.collection.find(
            {
                "status": BaccaratStatus.SHOWING.value,
                "revealUntil": {"$lte": now},
            }
        )
        return [self._mapper.baccarat_from_document(document) async for document in cursor]

    async def list_completed_since(self, since: datetime | None = None) -> list[BaccaratRecord]:
        query: dict = {"status": BaccaratStatus.COMPLETED.value}
        if since is not None:
            query["resolvedAt"] = {"$gte": since}
        cursor = self.collection.find(query).sort("resolvedAt", -1)
        return [self._mapper.baccarat_from_document(document) async for document in cursor]

    async def list_completed_by_player(self, discord_id: str) -> list[BaccaratRecord]:
        cursor = self.collection.find(
            {
                f"betsLamports.{discord_id}": {"$exists": True},
                "status": {
                    "$in": [BaccaratStatus.COMPLETED.value, BaccaratStatus.REFUNDED.value]
                },
            }
        ).sort("resolvedAt", -1)
        return [self._mapper.baccarat_from_document(document) async for document in cursor]

    async def list_live(self) -> list[BaccaratRecord]:
        cursor = self.collection.find({"status": {"$in": _LIVE}})
        return [self._mapper.baccarat_from_document(document) async for document in cursor]

    async def try_begin_settle(
        self,
        baccarat_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> BaccaratRecord | None:
        document = await self.collection.find_one_and_update(
            {
                "baccaratId": baccarat_id,
                "status": BaccaratStatus.OPEN.value,
                "openerId": {"$ne": None},
                "expiresAt": {"$lte": now},
            },
            {"$set": {"status": BaccaratStatus.SETTLING.value}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.baccarat_from_document(document) if document else None
