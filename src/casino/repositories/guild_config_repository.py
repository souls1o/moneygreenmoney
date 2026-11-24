from __future__ import annotations

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClientSession
from pymongo import ReturnDocument

from casino.db.mongo import COLLECTION_GUILD_CONFIGS
from casino.domain.enums import GuildConfigSlot
from casino.domain.records import GuildConfigRecord
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper


_SLOT_FIELDS: dict[GuildConfigSlot, str] = {
    GuildConfigSlot.OPEN_GAMES: "openGamesChannelId",
    GuildConfigSlot.OPEN_COINFLIP: "openCoinflipChannelId",
    GuildConfigSlot.OPEN_DICE: "openDiceChannelId",
    GuildConfigSlot.OPEN_BLACKJACK: "openBlackjackChannelId",
    GuildConfigSlot.OPEN_TARGET: "openTargetChannelId",
    GuildConfigSlot.BLACKJACK_OUTCOMES: "blackjackOutcomesChannelId",
    GuildConfigSlot.TARGET_OUTCOMES: "targetOutcomesChannelId",
    GuildConfigSlot.COINFLIP_OUTCOMES: "coinflipOutcomesChannelId",
    GuildConfigSlot.COINFLIP_CATEGORY: "coinflipCategoryId",
    GuildConfigSlot.DICE_OUTCOMES: "diceOutcomesChannelId",
    GuildConfigSlot.DICE_CATEGORY: "diceCategoryId",
    GuildConfigSlot.PROVABLY_FAIR: "provablyFairChannelId",
    GuildConfigSlot.DEPOSIT_ALERTS: "depositAlertsChannelId",
    GuildConfigSlot.WITHDRAWAL_ALERTS: "withdrawalAlertsChannelId",
    GuildConfigSlot.HOUSE_LEDGER: "houseLedgerChannelId",
    GuildConfigSlot.RAIN: "rainChannelId",
    GuildConfigSlot.RAIN_PING: "rainPingRoleId",
    GuildConfigSlot.VERIFY: "verifyChannelId",
    GuildConfigSlot.MEMBER_ROLE: "memberRoleId",
    GuildConfigSlot.INVESTOR_ROLE: "investorRoleId",
    GuildConfigSlot.SUPPORT: "supportChannelId",
    GuildConfigSlot.SUPPORT_CATEGORY: "supportCategoryId",
    GuildConfigSlot.SUPPORT_ROLE: "supportRoleId",
    GuildConfigSlot.SUPPORT_LOGS: "supportLogsChannelId",
    GuildConfigSlot.JACKPOT: "jackpotChannelId",
    GuildConfigSlot.JACKPOT_HISTORY: "jackpotHistoryChannelId",
    GuildConfigSlot.BACCARAT: "baccaratChannelId",
    GuildConfigSlot.BACCARAT_HISTORY: "baccaratHistoryChannelId",
    GuildConfigSlot.WAGER_RACE: "wagerRaceChannelId",
}


class GuildConfigRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_GUILD_CONFIGS)
        self._mapper = mapper

    async def get(self, guild_id: str, session: AsyncIOMotorClientSession | None = None) -> GuildConfigRecord:
        document = await self.collection.find_one({"guildId": guild_id}, session=session)
        if document is None:
            return GuildConfigRecord(guild_id=guild_id)
        return self._mapper.guild_config_from_document(document)

    async def set_slot(
        self,
        guild_id: str,
        slot: GuildConfigSlot,
        channel_id: str | None,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GuildConfigRecord:
        update: dict[str, object] = {
            "$set": {_SLOT_FIELDS[slot]: channel_id, "updatedAt": now},
            "$inc": {"version": 1},
            "$setOnInsert": {"guildId": guild_id},
        }
        if slot is GuildConfigSlot.PROVABLY_FAIR:
            update["$unset"] = {"provablyFairMessageId": ""}
        if slot is GuildConfigSlot.VERIFY:
            update["$unset"] = {"verifyMessageId": ""}
        if slot is GuildConfigSlot.SUPPORT:
            update["$unset"] = {"supportMessageId": ""}
        if slot is GuildConfigSlot.JACKPOT:
            update["$unset"] = {"jackpotMessageId": ""}
        if slot is GuildConfigSlot.WAGER_RACE:
            update["$unset"] = {"wagerRaceMessageId": ""}
        document = await self.collection.find_one_and_update(
            {"guildId": guild_id},
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.guild_config_from_document(document)

    async def take_next_table_number(
        self,
        guild_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> int:
        document = await self.collection.find_one_and_update(
            {"guildId": guild_id},
            {
                "$inc": {"nextTableNumber": 1},
                "$setOnInsert": {"guildId": guild_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return int((document or {}).get("nextTableNumber") or 1)

    async def set_pf_message(
        self,
        guild_id: str,
        message_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GuildConfigRecord:
        document = await self.collection.find_one_and_update(
            {"guildId": guild_id},
            {
                "$set": {"provablyFairMessageId": message_id, "updatedAt": now},
                "$inc": {"version": 1},
                "$setOnInsert": {"guildId": guild_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.guild_config_from_document(document)

    async def set_verify_message(
        self,
        guild_id: str,
        message_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GuildConfigRecord:
        document = await self.collection.find_one_and_update(
            {"guildId": guild_id},
            {
                "$set": {"verifyMessageId": message_id, "updatedAt": now},
                "$inc": {"version": 1},
                "$setOnInsert": {"guildId": guild_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.guild_config_from_document(document)

    async def take_next_ticket_number(
        self,
        guild_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> int:
        document = await self.collection.find_one_and_update(
            {"guildId": guild_id},
            {
                "$inc": {"nextTicketNumber": 1},
                "$setOnInsert": {"guildId": guild_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return int((document or {}).get("nextTicketNumber") or 1)

    async def set_support_message(
        self,
        guild_id: str,
        message_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GuildConfigRecord:
        document = await self.collection.find_one_and_update(
            {"guildId": guild_id},
            {
                "$set": {"supportMessageId": message_id, "updatedAt": now},
                "$inc": {"version": 1},
                "$setOnInsert": {"guildId": guild_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.guild_config_from_document(document)

    async def set_baccarat_message(
        self,
        guild_id: str,
        message_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GuildConfigRecord:
        document = await self.collection.find_one_and_update(
            {"guildId": guild_id},
            {
                "$set": {"baccaratMessageId": message_id, "updatedAt": now},
                "$inc": {"version": 1},
                "$setOnInsert": {"guildId": guild_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.guild_config_from_document(document)

    async def list_wager_race_guild_ids(
        self, session: AsyncIOMotorClientSession | None = None
    ) -> list[str]:
        cursor = self.collection.find(
            {"wagerRaceChannelId": {"$nin": [None, ""]}},
            {"guildId": 1},
            session=session,
        )
        return [str(document["guildId"]) async for document in cursor if document.get("guildId")]

    async def set_wager_race_prizes(
        self,
        guild_id: str,
        *,
        first_usd,
        second_usd,
        third_usd,
        fourth_fifth_usd,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GuildConfigRecord:
        document = await self.collection.find_one_and_update(
            {"guildId": guild_id},
            {
                "$set": {
                    "wagerRaceFirstUsd": MongoRepository.decimal_to_str(first_usd),
                    "wagerRaceSecondUsd": MongoRepository.decimal_to_str(second_usd),
                    "wagerRaceThirdUsd": MongoRepository.decimal_to_str(third_usd),
                    "wagerRaceFourthFifthUsd": MongoRepository.decimal_to_str(fourth_fifth_usd),
                    "updatedAt": now,
                },
                "$inc": {"version": 1},
                "$setOnInsert": {"guildId": guild_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.guild_config_from_document(document)

    async def set_wager_race_message(
        self,
        guild_id: str,
        message_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GuildConfigRecord:
        document = await self.collection.find_one_and_update(
            {"guildId": guild_id},
            {
                "$set": {"wagerRaceMessageId": message_id, "updatedAt": now},
                "$inc": {"version": 1},
                "$setOnInsert": {"guildId": guild_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.guild_config_from_document(document)

    async def set_jackpot_message(
        self,
        guild_id: str,
        message_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GuildConfigRecord:
        document = await self.collection.find_one_and_update(
            {"guildId": guild_id},
            {
                "$set": {"jackpotMessageId": message_id, "updatedAt": now},
                "$inc": {"version": 1},
                "$setOnInsert": {"guildId": guild_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.guild_config_from_document(document)
