from __future__ import annotations

from casino.domain.enums import TicketCategory
from casino.domain.records import TicketRecord
from casino.errors import NotFoundError, TicketError
from casino.logging.logger import get_logger
from casino.repositories.ticket_repository import TicketRepository
from casino.services.guild_config_service import GuildConfigService
from casino.utils.clock import Clock
from casino.utils.identifiers import IdentifierFactory

_MAX_SUBJECT = 80
_MAX_BODY = 1000


class TicketService:
    def __init__(
        self,
        tickets: TicketRepository,
        guild_configs: GuildConfigService,
        identifiers: IdentifierFactory,
        clock: Clock,
    ) -> None:
        self._tickets = tickets
        self._guilds = guild_configs
        self._ids = identifiers
        self._clock = clock
        self._logger = get_logger("casino.ticket")

    async def get(self, ticket_id: str) -> TicketRecord:
        ticket = await self._tickets.find_by_id(ticket_id)
        if ticket is None:
            raise NotFoundError("That ticket was not found.")
        return ticket

    async def get_by_channel(self, channel_id: str) -> TicketRecord:
        ticket = await self._tickets.find_by_channel(channel_id)
        if ticket is None:
            raise NotFoundError("This channel is not a support ticket.")
        return ticket

    async def create(
        self,
        *,
        guild_id: str,
        opener_id: str,
        category: TicketCategory,
        subject: str,
        body: str,
    ) -> TicketRecord:
        existing = await self._tickets.find_open_by_opener(guild_id, opener_id)
        if existing is not None:
            raise TicketError("You already have an open ticket. Use that channel until staff close it.")
        cleaned_subject = _clean(subject, _MAX_SUBJECT, "a short subject")
        cleaned_body = _clean(body, _MAX_BODY, "what you need help with")
        number = await self._guilds.take_next_ticket_number(guild_id)
        ticket = TicketRecord(
            ticket_id=self._ids.ticket_id(),
            guild_id=guild_id,
            opener_id=opener_id,
            category=category,
            number=number,
            subject=cleaned_subject,
            body=cleaned_body,
            created_at=self._clock.now(),
        )
        await self._tickets.insert(ticket)
        self._logger.info(
            "ticket_created",
            ticketId=ticket.ticket_id,
            guildId=guild_id,
            openerId=opener_id,
            category=category.value,
            number=number,
        )
        return ticket

    async def attach_channel(self, ticket_id: str, channel_id: str) -> TicketRecord:
        ticket = await self._tickets.set_channel(ticket_id, channel_id, self._clock.now())
        if ticket is None:
            raise NotFoundError("That ticket was not found.")
        return ticket

    async def append_chat(self, channel_id: str, event: dict) -> None:
        await self._tickets.append_chat_by_channel(channel_id, event)

    async def close(self, ticket_id: str, actor_id: str, reason: str = "") -> TicketRecord:
        ticket = await self.get(ticket_id)
        if not ticket.is_open():
            raise TicketError("This ticket is already closed.")
        closed = await self._tickets.mark_closed(
            ticket_id,
            actor_id,
            reason.strip()[:300],
            self._clock.now(),
        )
        if closed is None:
            raise TicketError("This ticket was updated by someone else. Try again.")
        self._logger.info("ticket_closed", ticketId=ticket_id, actorId=actor_id)
        return closed


def _clean(raw: str, limit: int, label: str) -> str:
    text = " ".join(raw.split())
    if not text:
        raise TicketError(f"Enter {label}.")
    if len(text) > limit:
        raise TicketError(f"Keep {label} under {limit} characters.")
    return text
