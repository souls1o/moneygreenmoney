from __future__ import annotations

from dataclasses import dataclass

from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.enums import LedgerEntryType, TransactionType
from casino.domain.records import UserRecord
from casino.errors import ValidationError
from casino.logging.logger import LogContext, get_logger
from casino.services.balance_service import BalanceService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.utils.money import SolAmount


@dataclass(frozen=True, slots=True)
class TipResult:
    sender: UserRecord
    recipient: UserRecord
    amount: SolAmount


class TipService:
    def __init__(
        self,
        user_service: UserService,
        balance_service: BalanceService,
        price_service: PriceService,
        unit_of_work: UnitOfWorkFactory,
    ) -> None:
        self._users = user_service
        self._balances = balance_service
        self._prices = price_service
        self._uow = unit_of_work
        self._logger = get_logger("casino.tip")

    async def send(self, sender_id: str, recipient_id: str, amount: SolAmount) -> TipResult:
        if sender_id == recipient_id:
            raise ValidationError("You cannot tip yourself.")
        if amount.is_zero():
            raise ValidationError("Enter a tip amount greater than zero.")
        await self._users.get_or_create(sender_id)
        await self._users.get_or_create(recipient_id)
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=sender_id):
            async with self._uow.begin() as uow:
                sender_mut = await self._balances.debit(
                    user_id=sender_id,
                    amount=amount,
                    entry_type=LedgerEntryType.TIP,
                    transaction_type=TransactionType.TIP,
                    price_usd=usd_price,
                    description=f"Tip to {recipient_id}",
                    session=uow.session,
                )
                recipient_mut = await self._balances.credit(
                    user_id=recipient_id,
                    amount=amount,
                    entry_type=LedgerEntryType.TIP,
                    transaction_type=TransactionType.TIP,
                    price_usd=usd_price,
                    description=f"Tip from {sender_id}",
                    session=uow.session,
                )
            self._logger.info(
                "tip_sent",
                recipientId=recipient_id,
                amountLamports=amount.lamports,
            )
            return TipResult(
                sender=sender_mut.user,
                recipient=recipient_mut.user,
                amount=amount,
            )
