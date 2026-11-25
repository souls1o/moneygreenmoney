from __future__ import annotations

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClientSession
from pymongo.errors import DuplicateKeyError

from casino.db.mongo import COLLECTION_TRANSACTIONS
from casino.domain.enums import TransactionStatus, TransactionType
from casino.domain.records import HOUSE_USER_ID, TransactionRecord
from casino.errors import AlreadyProcessedError
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper


class TransactionRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_TRANSACTIONS)
        self._mapper = mapper

    async def insert(
        self, transaction: TransactionRecord, session: AsyncIOMotorClientSession | None = None
    ) -> None:
        try:
            await self.collection.insert_one(
                self._mapper.transaction_to_document(transaction), session=session
            )
        except DuplicateKeyError as exc:
            raise AlreadyProcessedError("This transaction was already recorded.") from exc

    async def find_by_signature(
        self, signature: str, session: AsyncIOMotorClientSession | None = None
    ) -> TransactionRecord | None:
        document = await self.collection.find_one({"solanaTxSignature": signature}, session=session)
        return self._mapper.transaction_from_document(document) if document else None

    async def find_by_id(
        self, transaction_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> TransactionRecord | None:
        document = await self.collection.find_one({"transactionId": transaction_id}, session=session)
        return self._mapper.transaction_from_document(document) if document else None

    async def find_latest_withdrawal(
        self, user_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> TransactionRecord | None:
        document = await self.collection.find_one(
            {"userId": user_id, "type": TransactionType.WITHDRAWAL.value},
            sort=[("createdAt", -1)],
            session=session,
        )
        return self._mapper.transaction_from_document(document) if document else None

    async def sum_player_deposited_since(self, discord_id: str, since: datetime) -> int:
        pipeline = [
            {
                "$match": {
                    "userId": discord_id,
                    "type": TransactionType.DEPOSIT.value,
                    "status": TransactionStatus.CONFIRMED.value,
                    "createdAt": {"$gte": since},
                }
            },
            {"$group": {"_id": None, "total": {"$sum": "$amountLamports"}}},
        ]
        document = await self.aggregate_first(pipeline)
        if not document:
            return 0
        return int(document.get("total") or 0)

    async def list_player_flows_since(self, since: datetime | None = None) -> list[TransactionRecord]:
        query: dict[str, object] = {
            "type": {"$in": [TransactionType.DEPOSIT.value, TransactionType.WITHDRAWAL.value]},
            "status": TransactionStatus.CONFIRMED.value,
            "userId": {"$nin": [None, HOUSE_USER_ID]},
        }
        if since is not None:
            query["createdAt"] = {"$gte": since}
        documents = await self.collection.find(query).to_list(None)
        return [self._mapper.transaction_from_document(document) for document in documents]

    async def attach_signature(
        self,
        transaction_id: str,
        signature: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"transactionId": transaction_id},
            {"$set": {"solanaTxSignature": signature}},
            session=session,
        )
