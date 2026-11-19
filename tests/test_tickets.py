from __future__ import annotations

from datetime import datetime, timezone

import pytest

from casino.domain.enums import TicketCategory, TicketStatus
from casino.errors import TicketError
from casino.services.ticket_service import TicketService
from casino.utils.identifiers import IdentifierFactory
from tests.conftest import FrozenClock
from tests.fakes import MemoryTicketRepository


class StubGuildConfigs:
    def __init__(self) -> None:
        self.next_number = 0

    async def take_next_ticket_number(self, guild_id: str) -> int:
        self.next_number += 1
        return self.next_number


def _service() -> tuple[TicketService, MemoryTicketRepository]:
    repo = MemoryTicketRepository()
    service = TicketService(repo, StubGuildConfigs(), IdentifierFactory(), FrozenClock())
    return service, repo


@pytest.mark.asyncio
async def test_create_and_close_ticket() -> None:
    service, repo = _service()
    ticket = await service.create(
        guild_id="1",
        opener_id="player",
        category=TicketCategory.DEPOSIT,
        subject="Missing SOL",
        body="I deposited 1 SOL and it is not in my balance.",
    )
    assert ticket.number == 1
    assert ticket.status is TicketStatus.OPEN
    closed = await service.close(ticket.ticket_id, "staff", "Credited")
    assert closed.status is TicketStatus.CLOSED
    assert closed.close_reason == "Credited"
    stored = await repo.find_by_id(ticket.ticket_id)
    assert stored is not None
    assert stored.status is TicketStatus.CLOSED


@pytest.mark.asyncio
async def test_one_open_ticket_per_player() -> None:
    service, _repo = _service()
    await service.create(
        guild_id="1",
        opener_id="player",
        category=TicketCategory.OTHER,
        subject="Help",
        body="Need a human.",
    )
    with pytest.raises(TicketError, match="already have an open ticket"):
        await service.create(
            guild_id="1",
            opener_id="player",
            category=TicketCategory.GAME,
            subject="Again",
            body="Second ticket.",
        )


@pytest.mark.asyncio
async def test_reject_blank_subject_and_keep_chat_log() -> None:
    service, repo = _service()
    with pytest.raises(TicketError, match="short subject"):
        await service.create(
            guild_id="1",
            opener_id="player",
            category=TicketCategory.ACCOUNT,
            subject="   ",
            body="Details",
        )
    ticket = await service.create(
        guild_id="1",
        opener_id="player",
        category=TicketCategory.ACCOUNT,
        subject="Wallet",
        body="Need to change my withdrawal wallet.",
    )
    ticket = await service.attach_channel(ticket.ticket_id, "chan-1")
    now = datetime(2026, 11, 18, 16, 40, tzinfo=timezone.utc)
    await service.append_chat(
        "chan-1",
        {
            "kind": "created",
            "messageId": "m1",
            "authorId": "player",
            "authorName": "player",
            "content": "still waiting",
            "previousContent": "",
            "attachments": [],
            "at": now,
        },
    )
    await service.append_chat(
        "chan-1",
        {
            "kind": "edited",
            "messageId": "m1",
            "authorId": "player",
            "authorName": "player",
            "content": "still waiting on this",
            "previousContent": "still waiting",
            "attachments": [],
            "at": now,
        },
    )
    await service.append_chat(
        "chan-1",
        {
            "kind": "deleted",
            "messageId": "m1",
            "authorId": "player",
            "authorName": "player",
            "content": "still waiting on this",
            "previousContent": "",
            "attachments": [],
            "at": now,
        },
    )
    closed = await service.close(ticket.ticket_id, "staff", "Done")
    assert len(closed.chat_log) == 3
    assert closed.chat_log[1]["kind"] == "edited"
    assert closed.chat_log[2]["kind"] == "deleted"
    stored = await repo.find_by_id(ticket.ticket_id)
    assert stored is not None
    assert len(stored.chat_log) == 3


@pytest.mark.asyncio
async def test_close_keeps_chat_log() -> None:
    from casino.discord_bot.presentation.tickets import TicketPresenter

    service, _repo = _service()
    ticket = await service.create(
        guild_id="1",
        opener_id="player",
        category=TicketCategory.GAME,
        subject="Roll issue",
        body="Dice table vanished.",
    )
    ticket = await service.attach_channel(ticket.ticket_id, "chan-2")
    await service.append_chat(
        "chan-2",
        {
            "kind": "created",
            "messageId": "m2",
            "authorId": "staff",
            "authorName": "mod#0001",
            "content": "looking now",
            "previousContent": "",
            "attachments": [],
            "at": datetime(2026, 11, 18, 17, 0, tzinfo=timezone.utc),
        },
    )
    closed = await service.close(ticket.ticket_id, "staff", "Fixed")
    presenter = TicketPresenter.__new__(TicketPresenter)
    text = presenter.format_transcript(closed)
    assert "Dice table vanished." in text
    assert "looking now" in text
    assert "mod#0001" in text
