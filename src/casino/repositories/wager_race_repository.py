from __future__ import annotations

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClientSession
from pymongo import ReturnDocument

from casino.db.mongo import COLLECTION_WAGER_RACES
from casino.domain.enums import WagerRaceStatus
from casino.domain.records import WagerRaceRecord
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper


class WagerRaceRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_WAGER_RACES)
        self._mapper = mapper

    async def insert(self, race: WagerRaceRecord, session: AsyncIOMotorClientSession | None = None) -> None:
        await self.collection.insert_one(self._mapper.wager_race_to_document(race), session=session)

    async def find_by_id(
        self, race_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> WagerRaceRecord | None:
        document = await self.collection.find_one({"raceId": race_id}, session=session)
        return self._mapper.wager_race_from_document(document) if document else None

    async def find_open(
        self, guild_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> WagerRaceRecord | None:
        document = await self.collection.find_one(
            {"guildId": guild_id, "status": WagerRaceStatus.OPEN.value},
            session=session,
        )
        return self._mapper.wager_race_from_document(document) if document else None

    async def replace(self, race: WagerRaceRecord, session: AsyncIOMotorClientSession | None = None) -> bool:
        result = await self.collection.replace_one(
            {"raceId": race.race_id, "version": race.version},
            {**self._mapper.wager_race_to_document(race), "version": race.version + 1},
            session=session,
        )
        if result.matched_count == 1:
            race.version += 1
            return True
        return False

    async def add_wager(
        self,
        race_id: str,
        user_id: str,
        lamports: int,
        session: AsyncIOMotorClientSession | None = None,
    ) -> WagerRaceRecord | None:
        document = await self.collection.find_one_and_update(
            {"raceId": race_id, "status": WagerRaceStatus.OPEN.value},
            {"$inc": {f"wagersLamports.{user_id}": int(lamports), "version": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.wager_race_from_document(document) if document else None

    async def set_listing_ref(
        self,
        race_id: str,
        channel_id: str,
        message_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"raceId": race_id},
            {"$set": {"channelId": channel_id, "messageId": message_id}},
            session=session,
        )

    async def list_open(self, session: AsyncIOMotorClientSession | None = None) -> list[WagerRaceRecord]:
        cursor = self.collection.find({"status": WagerRaceStatus.OPEN.value}, session=session)
        return [self._mapper.wager_race_from_document(document) async for document in cursor]

    async def list_due(
        self, now: datetime, session: AsyncIOMotorClientSession | None = None
    ) -> list[WagerRaceRecord]:
        cursor = self.collection.find(
            {"status": WagerRaceStatus.OPEN.value, "endsAt": {"$lte": now}},
            session=session,
        )
        return [self._mapper.wager_race_from_document(document) async for document in cursor]
