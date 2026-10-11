from __future__ import annotations

from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClientSession
from pymongo import ReturnDocument

from casino.db.mongo import COLLECTION_GAMES
from casino.domain.enums import CoinSide, GameStatus
from casino.domain.records import GameRecord, is_house_seat
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper


class GameRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_GAMES)
        self._mapper = mapper

    async def insert(self, game: GameRecord, session: AsyncIOMotorClientSession | None = None) -> None:
        await self.collection.insert_one(self._mapper.game_to_document(game), session=session)

    async def find_by_id(
        self, game_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> GameRecord | None:
        document = await self.collection.find_one({"gameId": game_id}, session=session)
        return self._mapper.game_from_document(document) if document else None

    def _player_completed_query(self, discord_id: str) -> dict[str, Any]:
        return {
            "status": GameStatus.COMPLETED.value,
            "$or": [{"headsIds": discord_id}, {"tailsIds": discord_id}],
        }

    async def list_by_player(self, discord_id: str, limit: int = 10, skip: int = 0) -> list[GameRecord]:
        cursor = (
            self.collection.find(self._player_completed_query(discord_id))
            .sort("resolvedAt", -1)
            .skip(skip)
            .limit(limit)
        )
        return [self._mapper.game_from_document(doc) async for doc in cursor]

    async def count_completed_by_player(self, discord_id: str) -> int:
        return await self.collection.count_documents(self._player_completed_query(discord_id))

    async def list_completed_by_player(self, discord_id: str) -> list[GameRecord]:
        cursor = self.collection.find(self._player_completed_query(discord_id)).sort("resolvedAt", -1)
        return [self._mapper.game_from_document(doc) async for doc in cursor]

    async def list_open(self, limit: int = 20) -> list[GameRecord]:
        cursor = self.collection.find({"status": GameStatus.PENDING.value}).sort("createdAt", -1).limit(limit)
        return [self._mapper.game_from_document(doc) async for doc in cursor]

    async def try_add_to_side(
        self,
        game_id: str,
        player_id: str,
        side: CoinSide,
        seats_per_side: int,
        *,
        house_seat: bool = False,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GameRecord | None:
        side_field = "headsIds" if side is CoinSide.HEADS else "tailsIds"
        update: dict[str, Any] = {
            "$push": {side_field: player_id},
            "$inc": {"version": 1},
        }
        if house_seat or is_house_seat(player_id):
            update["$push"]["houseSeatIds"] = player_id
        document = await self.collection.find_one_and_update(
            {
                "gameId": game_id,
                "status": GameStatus.PENDING.value,
                "headsIds": {"$nin": [player_id]},
                "tailsIds": {"$nin": [player_id]},
                "$expr": {"$lt": [{"$size": {"$ifNull": [f"${side_field}", []]}}, seats_per_side]},
            },
            update,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.game_from_document(document) if document else None

    async def try_mark_ready(
        self,
        game_id: str,
        player_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GameRecord | None:
        document = await self.collection.find_one_and_update(
            {
                "gameId": game_id,
                "status": GameStatus.PENDING.value,
                "readyIds": {"$nin": [player_id]},
                "$or": [{"headsIds": player_id}, {"tailsIds": player_id}],
            },
            {
                "$addToSet": {"readyIds": player_id},
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.game_from_document(document) if document else None

    async def list_completed(self, guild_id: str, limit: int = 10) -> list[GameRecord]:
        cursor = (
            self.collection.find(
                {"guildId": guild_id, "status": GameStatus.COMPLETED.value}
            )
            .sort("resolvedAt", -1)
            .limit(limit)
        )
        return [self._mapper.game_from_document(doc) async for doc in cursor]

    async def list_completed_since(self, since: datetime | None = None) -> list[GameRecord]:
        query: dict[str, Any] = {"status": GameStatus.COMPLETED.value}
        if since is not None:
            query["resolvedAt"] = {"$gte": since}
        cursor = self.collection.find(query).sort("resolvedAt", -1)
        return [self._mapper.game_from_document(doc) async for doc in cursor]

    async def replace(self, game: GameRecord, session: AsyncIOMotorClientSession | None = None) -> bool:
        result = await self.collection.replace_one(
            {"gameId": game.game_id, "version": game.version},
            {**self._mapper.game_to_document(game), "version": game.version + 1},
            session=session,
        )
        if result.matched_count == 1:
            game.version += 1
            return True
        return False

    async def set_listing_ref(
        self,
        game_id: str,
        channel_id: str,
        message_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"gameId": game_id},
            {"$set": {"channelId": channel_id, "messageId": message_id}},
            session=session,
        )

    async def set_private_ref(
        self,
        game_id: str,
        channel_id: str,
        message_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"gameId": game_id},
            {"$set": {"privateChannelId": channel_id, "privateMessageId": message_id}},
            session=session,
        )

    async def set_table_number(
        self,
        game_id: str,
        table_number: int,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"gameId": game_id},
            {"$set": {"tableNumber": table_number}},
            session=session,
        )

    async def find_pending_in_channel(
        self, channel_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> GameRecord | None:
        document = await self.collection.find_one(
            {"privateChannelId": channel_id, "status": GameStatus.PENDING.value},
            session=session,
        )
        return self._mapper.game_from_document(document) if document else None

    async def find_active_in_channel(
        self, channel_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> GameRecord | None:
        document = await self.collection.find_one(
            {"privateChannelId": channel_id, "status": GameStatus.ACTIVE.value},
            session=session,
        )
        return self._mapper.game_from_document(document) if document else None

    async def list_due_channel_deletes(self, now: datetime, limit: int = 50) -> list[GameRecord]:
        cursor = self.collection.find({"channelDeleteAt": {"$lte": now}}).limit(limit)
        return [self._mapper.game_from_document(doc) async for doc in cursor]

    async def clear_channel_delete_at(
        self,
        game_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"gameId": game_id},
            {"$unset": {"channelDeleteAt": ""}},
            session=session,
        )

    async def set_guild_id(
        self,
        game_id: str,
        guild_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"gameId": game_id},
            {"$set": {"guildId": guild_id}},
            session=session,
        )

    async def try_remove_player(
        self,
        game_id: str,
        player_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GameRecord | None:
        document = await self.collection.find_one_and_update(
            {
                "gameId": game_id,
                "status": GameStatus.PENDING.value,
                "$or": [{"headsIds": player_id}, {"tailsIds": player_id}],
            },
            {
                "$pull": {
                    "headsIds": player_id,
                    "tailsIds": player_id,
                    "readyIds": player_id,
                    "houseSeatIds": player_id,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.game_from_document(document) if document else None

    async def list_due_auto_flips(self, now: datetime, limit: int = 50) -> list[GameRecord]:
        cursor = self.collection.find(
            {
                "status": GameStatus.PENDING.value,
                "autoFlipAt": {"$lte": now},
            }
        ).limit(limit)
        return [self._mapper.game_from_document(doc) async for doc in cursor]

    async def cancel_if_pending(
        self,
        game_id: str,
        status: GameStatus,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> GameRecord | None:
        document = await self.collection.find_one_and_update(
            {"gameId": game_id, "status": GameStatus.PENDING.value},
            {
                "$set": {"status": status.value, "resolvedAt": now},
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.game_from_document(document) if document else None
