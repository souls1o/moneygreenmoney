from __future__ import annotations

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClientSession
from pymongo import ReturnDocument

from casino.db.mongo import COLLECTION_JACKPOTS
from casino.domain.enums import JackpotStatus
from casino.domain.records import JackpotRecord
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper


class JackpotRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_JACKPOTS)
        self._mapper = mapper

    async def insert(self, jackpot: JackpotRecord, session: AsyncIOMotorClientSession | None = None) -> None:
        await self.collection.insert_one(self._mapper.jackpot_to_document(jackpot), session=session)

    async def find_by_id(
        self, jackpot_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> JackpotRecord | None:
        document = await self.collection.find_one({"jackpotId": jackpot_id}, session=session)
        return self._mapper.jackpot_from_document(document) if document else None

    async def find_live(
        self, guild_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> JackpotRecord | None:
        document = await self.collection.find_one(
            {
                "guildId": guild_id,
                "status": {
                    "$in": [
                        JackpotStatus.OPEN.value,
                        JackpotStatus.SHOWING.value,
                        JackpotStatus.SETTLING.value,
                    ]
                },
            },
            session=session,
        )
        return self._mapper.jackpot_from_document(document) if document else None

    async def replace(
        self, jackpot: JackpotRecord, session: AsyncIOMotorClientSession | None = None
    ) -> bool:
        result = await self.collection.replace_one(
            {"jackpotId": jackpot.jackpot_id, "version": jackpot.version},
            {**self._mapper.jackpot_to_document(jackpot), "version": jackpot.version + 1},
            session=session,
        )
        if result.matched_count == 1:
            jackpot.version += 1
            return True
        return False

    async def set_listing_ref(
        self,
        jackpot_id: str,
        channel_id: str,
        message_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"jackpotId": jackpot_id},
            {"$set": {"channelId": channel_id, "messageId": message_id}},
            session=session,
        )

    async def list_due(self, now: datetime) -> list[JackpotRecord]:
        cursor = self.collection.find(
            {
                "status": JackpotStatus.OPEN.value,
                "openerId": {"$ne": None},
                "expiresAt": {"$lte": now},
            }
        )
        return [self._mapper.jackpot_from_document(document) async for document in cursor]

    async def list_reveal_due(self, now: datetime) -> list[JackpotRecord]:
        cursor = self.collection.find(
            {
                "status": JackpotStatus.SHOWING.value,
                "revealUntil": {"$lte": now},
            }
        )
        return [self._mapper.jackpot_from_document(document) async for document in cursor]

    async def list_completed_by_player(self, discord_id: str) -> list[JackpotRecord]:
        cursor = self.collection.find(
            {
                f"entriesLamports.{discord_id}": {"$exists": True},
                "status": {
                    "$in": [JackpotStatus.COMPLETED.value, JackpotStatus.REFUNDED.value]
                },
            }
        ).sort("resolvedAt", -1)
        return [self._mapper.jackpot_from_document(document) async for document in cursor]

    async def list_live(self) -> list[JackpotRecord]:
        cursor = self.collection.find(
            {
                "status": {
                    "$in": [
                        JackpotStatus.OPEN.value,
                        JackpotStatus.SHOWING.value,
                        JackpotStatus.SETTLING.value,
                    ]
                }
            }
        )
        return [self._mapper.jackpot_from_document(document) async for document in cursor]

    async def try_begin_settle(
        self,
        jackpot_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> JackpotRecord | None:
        document = await self.collection.find_one_and_update(
            {
                "jackpotId": jackpot_id,
                "status": JackpotStatus.OPEN.value,
                "openerId": {"$ne": None},
                "expiresAt": {"$lte": now},
            },
            {"$set": {"status": JackpotStatus.SETTLING.value}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.jackpot_from_document(document) if document else None
