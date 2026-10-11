from __future__ import annotations

from decimal import Decimal

from motor.motor_asyncio import AsyncIOMotorClientSession

from casino.domain.enums import LedgerEntryType
from casino.domain.records import LedgerRecord
from casino.logging.logger import get_logger
from casino.repositories.ledger_repository import LedgerRepository
from casino.utils.clock import Clock
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import SolAmount


class LedgerService:
    """Append-only audit log. Every balance change must pass through here."""

    def __init__(
        self,
        ledger_repository: LedgerRepository,
        identifiers: IdentifierFactory,
        clock: Clock,
    ) -> None:
        self._ledger = ledger_repository
        self._identifiers = identifiers
        self._clock = clock
        self._logger = get_logger("casino.ledger")

    async def append(
        self,
        *,
        user_id: str,
        entry_type: LedgerEntryType,
        amount: SolAmount,
        signed_lamports: int,
        balance_after: SolAmount,
        price_usd_at_time: Decimal,
        description: str,
        related_game_id: str | None = None,
        related_tx_id: str | None = None,
        session: AsyncIOMotorClientSession | None = None,
    ) -> LedgerRecord:
        entry = LedgerRecord(
            entry_id=self._identifiers.ledger_id(),
            user_id=user_id,
            type=entry_type,
            amount_lamports=signed_lamports,
            balance_after_lamports=balance_after.lamports,
            price_usd_at_time=price_usd_at_time,
            description=description,
            related_game_id=related_game_id,
            related_tx_id=related_tx_id,
            timestamp=self._clock.now(),
        )
        await self._ledger.append(entry, session=session)
        self._logger.info(
            "ledger_appended",
            entryId=entry.entry_id,
            userId=user_id,
            type=entry_type.value,
            amountLamports=signed_lamports,
            balanceAfter=balance_after.lamports,
            relatedGameId=related_game_id,
            relatedTxId=related_tx_id,
        )
        return entry

    async def list_for_user(self, user_id: str, limit: int = 25) -> list[LedgerRecord]:
        return await self._ledger.list_for_user(user_id, limit=limit)

    async def list_for_game(self, game_id: str) -> list[LedgerRecord]:
        return await self._ledger.list_for_game(game_id)

    async def signed_balance_lamports(self, user_id: str) -> int:
        return await self._ledger.sum_signed_lamports(user_id)
