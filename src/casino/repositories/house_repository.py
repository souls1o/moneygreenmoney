from __future__ import annotations

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClientSession
from pymongo import ReturnDocument

from casino.db.mongo import COLLECTION_HOUSE
from casino.domain.enums import GameType
from casino.domain.records import HouseTreasuryRecord
from casino.errors import InsufficientHouseReserveError
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper
from casino.utils.money import SolAmount

HOUSE_DOCUMENT_NAME = "treasury"


class HouseRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_HOUSE)
        self._mapper = mapper

    async def get(self, session: AsyncIOMotorClientSession | None = None) -> HouseTreasuryRecord:
        document = await self.collection.find_one({"name": HOUSE_DOCUMENT_NAME}, session=session)
        if document is None:
            return HouseTreasuryRecord()
        return self._mapper.house_from_document(document)

    async def credit_fee(
        self, amount: SolAmount, now: datetime, session: AsyncIOMotorClientSession | None = None
    ) -> HouseTreasuryRecord:
        document = await self.collection.find_one_and_update(
            {"name": HOUSE_DOCUMENT_NAME},
            {
                "$inc": {
                    "solBalanceLamports": amount.lamports,
                    "totalFeesCollectedLamports": amount.lamports,
                    "version": 1,
                },
                "$set": {"updatedAt": now},
                "$setOnInsert": {"name": HOUSE_DOCUMENT_NAME},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.house_from_document(document)

    async def credit_reserve(
        self, amount: SolAmount, now: datetime, session: AsyncIOMotorClientSession | None = None
    ) -> HouseTreasuryRecord:
        document = await self.collection.find_one_and_update(
            {"name": HOUSE_DOCUMENT_NAME},
            {
                "$inc": {"solBalanceLamports": amount.lamports, "version": 1},
                "$set": {"updatedAt": now},
                "$setOnInsert": {"name": HOUSE_DOCUMENT_NAME, "totalFeesCollectedLamports": 0},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.house_from_document(document)

    async def debit_reserve(
        self, amount: SolAmount, now: datetime, session: AsyncIOMotorClientSession | None = None
    ) -> HouseTreasuryRecord:
        document = await self.collection.find_one_and_update(
            {"name": HOUSE_DOCUMENT_NAME, "solBalanceLamports": {"$gte": amount.lamports}},
            {
                "$inc": {"solBalanceLamports": -amount.lamports, "version": 1},
                "$set": {"updatedAt": now},
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if document is None:
            raise InsufficientHouseReserveError()
        return self._mapper.house_from_document(document)

    async def take_next_fair_nonce(
        self,
        session: AsyncIOMotorClientSession | None = None,
    ) -> int:
        document = await self.collection.find_one_and_update(
            {"name": HOUSE_DOCUMENT_NAME},
            {
                "$inc": {"nextFairNonce": 1},
                "$setOnInsert": {
                    "name": HOUSE_DOCUMENT_NAME,
                    "solBalanceLamports": 0,
                    "totalFeesCollectedLamports": 0,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return int((document or {}).get("nextFairNonce") or 1)

    async def take_next_game_number(
        self,
        game_type: GameType,
        session: AsyncIOMotorClientSession | None = None,
    ) -> int:
        field = f"nextGameNumbers.{game_type.value}"
        document = await self.collection.find_one_and_update(
            {"name": HOUSE_DOCUMENT_NAME},
            {
                "$inc": {field: 1},
                "$setOnInsert": {
                    "name": HOUSE_DOCUMENT_NAME,
                    "solBalanceLamports": 0,
                    "totalFeesCollectedLamports": 0,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        numbers = (document or {}).get("nextGameNumbers") or {}
        return int(numbers.get(game_type.value) or 1)
