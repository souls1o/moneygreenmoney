from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from motor.motor_asyncio import AsyncIOMotorClientSession

from casino.domain.enums import LedgerEntryType, TransactionStatus, TransactionType
from casino.domain.records import HOUSE_USER_ID, HouseTreasuryRecord, LedgerRecord, TransactionRecord, UserRecord
from casino.logging.logger import get_logger
from casino.repositories.house_repository import HouseRepository
from casino.repositories.transaction_repository import TransactionRepository
from casino.repositories.user_repository import UserRepository
from casino.services.ledger_service import LedgerService
from casino.utils.clock import Clock
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import SolAmount


@dataclass(frozen=True, slots=True)
class BalanceMutation:
    user: UserRecord
    ledger_entry: LedgerRecord
    transaction: TransactionRecord


class BalanceService:
    """Atomic credits and debits. Every mutation writes a ledger entry and a transaction row."""

    def __init__(
        self,
        user_repository: UserRepository,
        transaction_repository: TransactionRepository,
        house_repository: HouseRepository,
        ledger_service: LedgerService,
        identifiers: IdentifierFactory,
        clock: Clock,
    ) -> None:
        self._users = user_repository
        self._transactions = transaction_repository
        self._house = house_repository
        self._ledger = ledger_service
        self._identifiers = identifiers
        self._clock = clock
        self._logger = get_logger("casino.balance")

    async def credit(
        self,
        *,
        user_id: str,
        amount: SolAmount,
        entry_type: LedgerEntryType,
        transaction_type: TransactionType,
        price_usd: Decimal,
        description: str,
        game_id: str | None = None,
        solana_signature: str | None = None,
        deposited: bool = False,
        won: SolAmount | None = None,
        session: AsyncIOMotorClientSession | None = None,
    ) -> BalanceMutation:
        now = self._clock.now()
        user = await self._users.credit_balance(
            user_id,
            amount,
            deposited=amount if deposited else None,
            won=won,
            now=now,
            session=session,
        )
        tx = TransactionRecord(
            transaction_id=self._identifiers.transaction_id(),
            type=transaction_type,
            status=TransactionStatus.CONFIRMED,
            amount_lamports=amount.lamports,
            amount_usd=(amount.to_decimal() * price_usd),
            user_id=user_id,
            game_id=game_id,
            solana_tx_signature=solana_signature,
            metadata={"description": description},
            created_at=now,
        )
        await self._transactions.insert(tx, session=session)
        entry = await self._ledger.append(
            user_id=user_id,
            entry_type=entry_type,
            amount=amount,
            signed_lamports=amount.lamports,
            balance_after=user.sol_balance,
            price_usd_at_time=price_usd,
            description=description,
            related_game_id=game_id,
            related_tx_id=tx.transaction_id,
            session=session,
        )
        self._logger.info(
            "balance_credited",
            userId=user_id,
            amountLamports=amount.lamports,
            gameId=game_id,
            txSignature=solana_signature,
        )
        return BalanceMutation(user=user, ledger_entry=entry, transaction=tx)

    async def debit(
        self,
        *,
        user_id: str,
        amount: SolAmount,
        entry_type: LedgerEntryType,
        transaction_type: TransactionType,
        price_usd: Decimal,
        description: str,
        game_id: str | None = None,
        solana_signature: str | None = None,
        wagered: bool = False,
        withdrawn: bool = False,
        session: AsyncIOMotorClientSession | None = None,
    ) -> BalanceMutation:
        now = self._clock.now()
        user = await self._users.debit_balance(
            user_id,
            amount,
            wagered=amount if wagered else None,
            withdrawn=amount if withdrawn else None,
            now=now,
            session=session,
        )
        tx = TransactionRecord(
            transaction_id=self._identifiers.transaction_id(),
            type=transaction_type,
            status=TransactionStatus.CONFIRMED,
            amount_lamports=amount.lamports,
            amount_usd=(amount.to_decimal() * price_usd),
            user_id=user_id,
            game_id=game_id,
            solana_tx_signature=solana_signature,
            metadata={"description": description},
            created_at=now,
        )
        await self._transactions.insert(tx, session=session)
        entry = await self._ledger.append(
            user_id=user_id,
            entry_type=entry_type,
            amount=amount,
            signed_lamports=-amount.lamports,
            balance_after=user.sol_balance,
            price_usd_at_time=price_usd,
            description=description,
            related_game_id=game_id,
            related_tx_id=tx.transaction_id,
            session=session,
        )
        self._logger.info(
            "balance_debited",
            userId=user_id,
            amountLamports=amount.lamports,
            gameId=game_id,
        )
        return BalanceMutation(user=user, ledger_entry=entry, transaction=tx)

    async def credit_house_fee(
        self,
        *,
        amount: SolAmount,
        price_usd: Decimal,
        game_id: str,
        session: AsyncIOMotorClientSession | None = None,
    ) -> LedgerRecord:
        now = self._clock.now()
        treasury = await self._house.credit_fee(amount, now, session=session)
        tx = TransactionRecord(
            transaction_id=self._identifiers.transaction_id(),
            type=TransactionType.FEE,
            status=TransactionStatus.CONFIRMED,
            amount_lamports=amount.lamports,
            amount_usd=(amount.to_decimal() * price_usd),
            user_id=HOUSE_USER_ID,
            game_id=game_id,
            metadata={"description": "House fee"},
            created_at=now,
        )
        await self._transactions.insert(tx, session=session)
        return await self._ledger.append(
            user_id=HOUSE_USER_ID,
            entry_type=LedgerEntryType.FEE,
            amount=amount,
            signed_lamports=amount.lamports,
            balance_after=SolAmount.from_lamports(treasury.sol_balance_lamports),
            price_usd_at_time=price_usd,
            description=f"House fee for game {game_id}",
            related_game_id=game_id,
            related_tx_id=tx.transaction_id,
            session=session,
        )

    async def debit_house_reserve(
        self,
        *,
        amount: SolAmount,
        price_usd: Decimal,
        description: str,
        game_id: str | None = None,
        entry_type: LedgerEntryType = LedgerEntryType.BET,
        transaction_type: TransactionType = TransactionType.BET,
        session: AsyncIOMotorClientSession | None = None,
    ) -> tuple[HouseTreasuryRecord, LedgerRecord]:
        now = self._clock.now()
        treasury = await self._house.debit_reserve(amount, now, session=session)
        tx = TransactionRecord(
            transaction_id=self._identifiers.transaction_id(),
            type=transaction_type,
            status=TransactionStatus.CONFIRMED,
            amount_lamports=amount.lamports,
            amount_usd=(amount.to_decimal() * price_usd),
            user_id=HOUSE_USER_ID,
            game_id=game_id,
            metadata={"description": description},
            created_at=now,
        )
        await self._transactions.insert(tx, session=session)
        entry = await self._ledger.append(
            user_id=HOUSE_USER_ID,
            entry_type=entry_type,
            amount=amount,
            signed_lamports=-amount.lamports,
            balance_after=SolAmount.from_lamports(treasury.sol_balance_lamports),
            price_usd_at_time=price_usd,
            description=description,
            related_game_id=game_id,
            related_tx_id=tx.transaction_id,
            session=session,
        )
        self._logger.info(
            "house_reserve_debited",
            amountLamports=amount.lamports,
            gameId=game_id,
        )
        return treasury, entry

    async def credit_house_reserve(
        self,
        *,
        amount: SolAmount,
        entry_type: LedgerEntryType,
        transaction_type: TransactionType,
        price_usd: Decimal,
        description: str,
        game_id: str | None = None,
        solana_signature: str | None = None,
        session: AsyncIOMotorClientSession | None = None,
    ) -> tuple[HouseTreasuryRecord, LedgerRecord]:
        now = self._clock.now()
        treasury = await self._house.credit_reserve(amount, now, session=session)
        tx = TransactionRecord(
            transaction_id=self._identifiers.transaction_id(),
            type=transaction_type,
            status=TransactionStatus.CONFIRMED,
            amount_lamports=amount.lamports,
            amount_usd=(amount.to_decimal() * price_usd),
            user_id=HOUSE_USER_ID,
            game_id=game_id,
            solana_tx_signature=solana_signature,
            metadata={"description": description},
            created_at=now,
        )
        await self._transactions.insert(tx, session=session)
        entry = await self._ledger.append(
            user_id=HOUSE_USER_ID,
            entry_type=entry_type,
            amount=amount,
            signed_lamports=amount.lamports,
            balance_after=SolAmount.from_lamports(treasury.sol_balance_lamports),
            price_usd_at_time=price_usd,
            description=description,
            related_game_id=game_id,
            related_tx_id=tx.transaction_id,
            session=session,
        )
        self._logger.info(
            "house_reserve_credited",
            amountLamports=amount.lamports,
            gameId=game_id,
            type=entry_type.value,
        )
        return treasury, entry
