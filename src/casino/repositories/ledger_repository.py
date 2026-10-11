from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClientSession

from casino.db.mongo import COLLECTION_LEDGER
from casino.domain.records import LedgerRecord
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper


class LedgerRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_LEDGER)
        self._mapper = mapper

    async def append(
        self, entry: LedgerRecord, session: AsyncIOMotorClientSession | None = None
    ) -> None:
        await self.collection.insert_one(self._mapper.ledger_to_document(entry), session=session)

    async def list_for_user(self, user_id: str, limit: int = 25) -> list[LedgerRecord]:
        cursor = self.collection.find({"userId": user_id}).sort("timestamp", -1).limit(limit)
        return [self._mapper.ledger_from_document(doc) async for doc in cursor]

    async def list_for_game(self, game_id: str) -> list[LedgerRecord]:
        cursor = self.collection.find({"relatedGameId": game_id}).sort("timestamp", 1)
        return [self._mapper.ledger_from_document(doc) async for doc in cursor]

    async def sum_signed_lamports(
        self,
        user_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> int:
        cursor = self.collection.aggregate(
            [
                {"$match": {"userId": user_id}},
                {"$group": {"_id": None, "total": {"$sum": "$amountLamports"}}},
            ],
            session=session,
        )
        document = await cursor.to_list(length=1)
        if not document:
            return 0
        return int(document[0].get("total") or 0)
