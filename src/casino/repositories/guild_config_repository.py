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
