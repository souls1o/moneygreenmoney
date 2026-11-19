from __future__ import annotations

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClientSession
from pymongo import ReturnDocument

from casino.db.mongo import COLLECTION_TICKETS
from casino.domain.enums import TicketStatus
from casino.domain.records import TicketRecord
from casino.repositories.base import MongoRepository
from casino.repositories.mappers import DocumentMapper


class TicketRepository(MongoRepository):
    def __init__(self, mongo, mapper: DocumentMapper) -> None:
        super().__init__(mongo, COLLECTION_TICKETS)
        self._mapper = mapper

    async def insert(self, ticket: TicketRecord, session: AsyncIOMotorClientSession | None = None) -> None:
        await self.collection.insert_one(self._mapper.ticket_to_document(ticket), session=session)

    async def find_by_id(
        self, ticket_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> TicketRecord | None:
        document = await self.collection.find_one({"ticketId": ticket_id}, session=session)
        return self._mapper.ticket_from_document(document) if document else None

    async def find_by_channel(
        self, channel_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> TicketRecord | None:
        document = await self.collection.find_one({"channelId": channel_id}, session=session)
        return self._mapper.ticket_from_document(document) if document else None

    async def find_open_by_opener(
        self, guild_id: str, opener_id: str, session: AsyncIOMotorClientSession | None = None
    ) -> TicketRecord | None:
        document = await self.collection.find_one(
            {
                "guildId": guild_id,
                "openerId": opener_id,
                "status": {"$in": [TicketStatus.OPEN.value, TicketStatus.CLAIMED.value]},
            },
            session=session,
        )
        return self._mapper.ticket_from_document(document) if document else None

    async def replace(self, ticket: TicketRecord, session: AsyncIOMotorClientSession | None = None) -> bool:
        result = await self.collection.replace_one(
            {"ticketId": ticket.ticket_id, "version": ticket.version},
            {**self._mapper.ticket_to_document(ticket), "version": ticket.version + 1},
            session=session,
        )
        if result.matched_count == 1:
            ticket.version += 1
            return True
        return False

    async def set_channel(
        self,
        ticket_id: str,
        channel_id: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> TicketRecord | None:
        document = await self.collection.find_one_and_update(
            {"ticketId": ticket_id},
            {"$set": {"channelId": channel_id}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.ticket_from_document(document) if document else None

    async def append_chat(
        self,
        ticket_id: str,
        event: dict,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"ticketId": ticket_id},
            {"$push": {"chatLog": event}},
            session=session,
        )

    async def append_chat_by_channel(
        self,
        channel_id: str,
        event: dict,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        await self.collection.update_one(
            {"channelId": channel_id},
            {"$push": {"chatLog": event}},
            session=session,
        )

    async def mark_closed(
        self,
        ticket_id: str,
        actor_id: str,
        reason: str,
        now: datetime,
        session: AsyncIOMotorClientSession | None = None,
    ) -> TicketRecord | None:
        document = await self.collection.find_one_and_update(
            {"ticketId": ticket_id, "status": {"$ne": TicketStatus.CLOSED.value}},
            {
                "$set": {
                    "status": TicketStatus.CLOSED.value,
                    "closedBy": actor_id,
                    "closedAt": now,
                    "closeReason": reason,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return self._mapper.ticket_from_document(document) if document else None
