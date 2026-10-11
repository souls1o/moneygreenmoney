from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from motor.motor_asyncio import AsyncIOMotorClientSession
from pymongo import ReturnDocument

from casino.db.mongo import COLLECTION_USERS
from casino.domain.enums import UserRole
from casino.domain.records import UserRecord
from casino.errors import InsufficientBalanceError, NotFoundError
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper
from casino.utils.money import SolAmount


class UserRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_USERS)
        self._mapper = mapper

    async def find_by_discord_id(
        self, discord_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> UserRecord | None:
        document = await self.collection.find_one({"discordId": discord_id}, session=session)
        return self._mapper.user_from_document(document) if document else None

    async def find_by_deposit_memo(
        self, memo: str, session: AsyncIOMotorClientSession | None = None
    ) -> UserRecord | None:
        document = await self.collection.find_one({"depositMemo": memo}, session=session)
        return self._mapper.user_from_document(document) if document else None

    async def find_by_deposit_address(
        self, address: str, session: AsyncIOMotorClientSession | None = None
    ) -> UserRecord | None:
        document = await self.collection.find_one({"depositAddress": address}, session=session)
        return self._mapper.user_from_document(document) if document else None

    async def list_deposit_wallets(
        self, session: AsyncIOMotorClientSession | None = None
    ) -> list[tuple[str, str]]:
        cursor = self.collection.find(
            {"depositAddress": {"$exists": True, "$nin": [None, ""]}},
            {"discordId": 1, "depositAddress": 1},
            session=session,
        )
        wallets: list[tuple[str, str]] = []
        async for document in cursor:
            address = document.get("depositAddress")
            discord_id = document.get("discordId")
            if address and discord_id:
                wallets.append((str(discord_id), str(address)))
        return wallets

    async def insert(self, user: UserRecord, session: AsyncIOMotorClientSession | None = None) -> None:
        await self.collection.insert_one(self._mapper.user_to_document(user), session=session)

    async def replace(self, user: UserRecord, session: AsyncIOMotorClientSession | None = None) -> None:
        result = await self.collection.replace_one(
            {"discordId": user.discord_id, "version": user.version - 1},
            self._mapper.user_to_document(user),
            session=session,
        )
        if result.matched_count != 1:
            raise NotFoundError("User could not be updated due to a concurrent modification.")

    async def credit_balance(
        self,
        discord_id: str,
        amount: SolAmount,
        *,
        deposited: SolAmount | None = None,
        won: SolAmount | None = None,
        fees_paid: SolAmount | None = None,
        games_played: int = 0,
        games_won: int = 0,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> UserRecord:
        increments: dict[str, int] = {
            "solBalanceLamports": amount.lamports,
            "version": 1,
        }
        if deposited:
            increments["totalDepositedLamports"] = deposited.lamports
        if won:
            increments["totalWonLamports"] = won.lamports
        if fees_paid:
            increments["totalFeesPaidLamports"] = fees_paid.lamports
        if games_played:
            increments["gamesPlayed"] = games_played
        if games_won:
            increments["gamesWon"] = games_won

        document = await self.collection.find_one_and_update(
            {"discordId": discord_id},
            {"$inc": increments, "$set": {"updatedAt": now}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if document is None:
            raise NotFoundError("User was not found.")
        return self._mapper.user_from_document(document)

    async def debit_balance(
        self,
        discord_id: str,
        amount: SolAmount,
        *,
        wagered: SolAmount | None = None,
        withdrawn: SolAmount | None = None,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> UserRecord:
        increments: dict[str, int] = {
            "solBalanceLamports": -amount.lamports,
            "version": 1,
        }
        if wagered:
            increments["totalWageredLamports"] = wagered.lamports
        if withdrawn:
            increments["totalWithdrawnLamports"] = withdrawn.lamports

        document = await self.collection.find_one_and_update(
            {"discordId": discord_id, "solBalanceLamports": {"$gte": amount.lamports}},
            {"$inc": increments, "$set": {"updatedAt": now}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if document is None:
            existing = await self.find_by_discord_id(discord_id, session=session)
            if existing is None:
                raise NotFoundError("User was not found.")
            raise InsufficientBalanceError("You do not have enough SOL for this action.")
        return self._mapper.user_from_document(document)

    async def set_currency(
        self, discord_id: str, currency: str, now: datetime, session: AsyncIOMotorClientSession | None = None
    ) -> UserRecord | None:
        document = await self.collection.find_one_and_update(
            {"discordId": discord_id},
            {"$set": {"displayCurrency": currency, "updatedAt": now}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.user_from_document(document) if document else None

    async def set_wallet(
        self, discord_id: str, wallet: str, now: datetime, session: AsyncIOMotorClientSession | None = None
    ) -> UserRecord | None:
        document = await self.collection.find_one_and_update(
            {"discordId": discord_id},
            {"$set": {"walletAddress": wallet, "updatedAt": now}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.user_from_document(document) if document else None

    async def set_deposit_address(
        self, discord_id: str, address: str, now: datetime, session: AsyncIOMotorClientSession | None = None
    ) -> UserRecord | None:
        document = await self.collection.find_one_and_update(
            {"discordId": discord_id},
            {"$set": {"depositAddress": address, "updatedAt": now}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.user_from_document(document) if document else None

    async def add_role(
        self, discord_id: str, role: UserRole, now: datetime, session: AsyncIOMotorClientSession | None = None
    ) -> UserRecord | None:
        document = await self.collection.find_one_and_update(
            {"discordId": discord_id},
            {"$addToSet": {"roles": role.value}, "$set": {"updatedAt": now}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.user_from_document(document) if document else None

    async def apply_game_stats(
        self,
        discord_id: str,
        *,
        wagered: SolAmount,
        wagered_usd: Decimal,
        max_bet_usd: Decimal,
        fees_paid: SolAmount,
        won: bool,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
        won_usd: Decimal = Decimal("0"),
    ) -> UserRecord:
        current = await self.find_by_discord_id(discord_id, session=session)
        if current is None:
            raise NotFoundError("User was not found.")
        new_wagered = current.total_wagered_usd + wagered_usd
        new_won_usd = current.total_won_usd + won_usd
        new_max = current.max_bet_usd if current.max_bet_usd >= max_bet_usd else max_bet_usd
        increments: dict[str, int] = {
            "gamesPlayed": 1,
            "gamesWon": 1 if won else 0,
            "totalWageredLamports": wagered.lamports,
            "totalFeesPaidLamports": fees_paid.lamports,
            "version": 1,
        }
        sets: dict[str, object] = {
            "totalWageredUsd": MongoRepository.decimal_to_str(new_wagered),
            "totalWonUsd": MongoRepository.decimal_to_str(new_won_usd),
            "maxBetUsd": MongoRepository.decimal_to_str(new_max),
            "updatedAt": now,
        }
        if current.referred_by:
            increments["referredWageredLamports"] = wagered.lamports
            sets["referredWageredUsd"] = MongoRepository.decimal_to_str(
                current.referred_wagered_usd + wagered_usd
            )
        document = await self.collection.find_one_and_update(
            {"discordId": discord_id},
            {"$inc": increments, "$set": sets},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if document is None:
            raise NotFoundError("User was not found.")
        return self._mapper.user_from_document(document)

    async def bind_referrer(
        self,
        discord_id: str,
        referrer_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> UserRecord | None:
        document = await self.collection.find_one_and_update(
            {
                "discordId": discord_id,
                "$or": [
                    {"referredBy": {"$exists": False}},
                    {"referredBy": None},
                    {"referredBy": ""},
                ],
            },
            {
                "$set": {
                    "referredBy": referrer_id,
                    "referredAt": now,
                    "referredWageredUsd": MongoRepository.decimal_to_str(Decimal("0")),
                    "referredWageredLamports": 0,
                    "updatedAt": now,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.user_from_document(document) if document else None

    async def list_referred_by(
        self,
        referrer_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> list[UserRecord]:
        cursor = self.collection.find({"referredBy": referrer_id}, session=session).sort(
            "referredAt", 1
        )
        return [self._mapper.user_from_document(document) async for document in cursor]

    async def add_affiliate_earnings(
        self,
        discord_id: str,
        amount: SolAmount,
        earned_usd: Decimal,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> UserRecord | None:
        current = await self.find_by_discord_id(discord_id, session=session)
        if current is None:
            return None
        document = await self.collection.find_one_and_update(
            {"discordId": discord_id},
            {
                "$inc": {
                    "affiliateEarnedLamports": amount.lamports,
                    "version": 1,
                },
                "$set": {
                    "affiliateEarnedUsd": MongoRepository.decimal_to_str(
                        current.affiliate_earned_usd + earned_usd
                    ),
                    "updatedAt": now,
                },
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.user_from_document(document) if document else None
