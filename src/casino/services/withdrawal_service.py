from __future__ import annotations

import asyncio
from dataclasses import dataclass

from casino.config.settings import Settings
from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.enums import LedgerEntryType, TransactionType
from casino.domain.records import UserRecord
from casino.errors import ConfigurationError, FeatureDisabledError, ValidationError, WithdrawalError
from casino.logging.logger import LogContext, get_logger
from casino.repositories.transaction_repository import TransactionRepository
from casino.services.balance_service import BalanceService
from casino.services.ledger_service import LedgerService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.solana_client.addresses import SolanaAddressValidator
from casino.solana_client.rpc import SWEEP_FEE_LAMPORTS, SolanaRpcGateway, SolanaTransferUnconfirmedError
from casino.utils.money import SolAmount


@dataclass(frozen=True, slots=True)
class WithdrawalResult:
    user: UserRecord
    amount: SolAmount
    destination: str
    signature: str


class WithdrawalService:
    """Debits the player, then pays SOL from the super wallet."""

    def __init__(
        self,
        settings: Settings,
        user_service: UserService,
        address_validator: SolanaAddressValidator,
        balance_service: BalanceService,
        ledger_service: LedgerService,
        price_service: PriceService,
        rpc: SolanaRpcGateway,
        transactions: TransactionRepository,
        unit_of_work: UnitOfWorkFactory,
    ) -> None:
        self._settings = settings
        self._users = user_service
        self._addresses = address_validator
        self._balances = balance_service
        self._ledger = ledger_service
        self._prices = price_service
        self._rpc = rpc
        self._transactions = transactions
        self._uow = unit_of_work
        self._on_sent = None
        self._logger = get_logger("casino.withdraw")

    def set_listener(self, on_sent) -> None:
        self._on_sent = on_sent

    def ensure_enabled(self) -> None:
        if not self._settings.enable_withdrawals:
            raise FeatureDisabledError("Withdrawals are not enabled yet.")
        if not self._rpc.super_address:
            raise ConfigurationError("Withdrawals need SUPER_WALLET_PRIVATE_KEY.")

    async def request(self, discord_id: str, amount: SolAmount, destination: str) -> WithdrawalResult:
        self.ensure_enabled()
        if amount.is_zero():
            raise ValidationError("Enter a withdrawal amount greater than zero.")
        await self._users.get_or_create(discord_id)
        wallet_raw = destination.strip()
        if not wallet_raw:
            raise ValidationError("Enter a destination wallet.")
        wallet = self._addresses.parse(wallet_raw)
        if wallet == self._rpc.super_address:
            raise ValidationError("Choose a different destination wallet.")

        ledger_balance = await self._ledger.signed_balance_lamports(discord_id)
        if amount.lamports > ledger_balance:
            raise WithdrawalError("This withdrawal is larger than your ledger balance.")

        payout = amount

        await self._rpc.collect_user_into_super(discord_id)
        super_balance = await self._rpc.get_balance(self._rpc.super_address)
        rent = await self._rpc.get_rent_exempt_lamports()
        needed = payout.lamports + SWEEP_FEE_LAMPORTS + rent
        if super_balance < needed:
            raise WithdrawalError(
                "The super wallet does not have enough on-chain SOL to pay this withdrawal right now."
            )

        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=discord_id):
            async with self._uow.begin() as uow:
                mutation = await self._balances.debit(
                    user_id=discord_id,
                    amount=amount,
                    entry_type=LedgerEntryType.WITHDRAWAL,
                    transaction_type=TransactionType.WITHDRAWAL,
                    price_usd=usd_price,
                    description=f"SOL withdrawal to {wallet}",
                    withdrawn=True,
                    session=uow.session,
                )
            try:
                signature = await self._rpc.send_from_super(wallet, payout)
            except SolanaTransferUnconfirmedError as exc:
                self._logger.warning(
                    "withdrawal_unconfirmed_refunding",
                    destination=wallet,
                    amountLamports=payout.lamports,
                    txSignature=exc.signature,
                )
                await self._refund(discord_id, amount, usd_price, wallet)
                raise WithdrawalError(
                    "Solana never confirmed this withdrawal on-chain. Your balance was refunded. Try again."
                ) from exc
            except Exception as exc:
                self._logger.exception(
                    "withdrawal_send_failed",
                    destination=wallet,
                    amountLamports=payout.lamports,
                    error=str(exc),
                )
                await self._refund(discord_id, amount, usd_price, wallet)
                raise WithdrawalError(_user_send_error(exc)) from exc
            try:
                await self._transactions.attach_signature(mutation.transaction.transaction_id, signature)
            except Exception:
                self._logger.exception("withdrawal_signature_attach_failed", txId=mutation.transaction.transaction_id)
            self._logger.info(
                "withdrawal_sent",
                amountLamports=payout.lamports,
                destination=wallet,
                txSignature=signature,
            )
            if self._on_sent is not None:
                try:
                    await self._on_sent(discord_id, payout, wallet, signature)
                except Exception:
                    self._logger.exception("withdrawal_alert_failed", userId=discord_id)
            return WithdrawalResult(
                user=mutation.user,
                amount=payout,
                destination=wallet,
                signature=signature,
            )

    async def complete_unconfirmed(self, discord_id: str) -> WithdrawalResult:
        """Pay an already-debited withdrawal from the super wallet without touching casino balance."""
        self.ensure_enabled()
        user = await self._users.get(discord_id)
        if user is None:
            raise WithdrawalError("That member has no casino account.")
        tx = await self._transactions.find_latest_withdrawal(discord_id)
        if tx is None:
            raise WithdrawalError("No withdrawal was found for that member.")
        wallet = self._addresses.parse(_destination_from_withdrawal(tx))
        payout = SolAmount.from_lamports(abs(int(tx.amount_lamports)))
        if payout.is_zero():
            raise WithdrawalError("That withdrawal amount is zero.")
        if tx.solana_tx_signature:
            try:
                outcome = await self._rpc.wait_for_signature(
                    tx.solana_tx_signature, attempts=8, delay=0.5
                )
            except Exception:
                outcome = None
            if outcome == "ok":
                return WithdrawalResult(
                    user=user,
                    amount=payout,
                    destination=wallet,
                    signature=tx.solana_tx_signature,
                )
        super_balance = await self._rpc.get_balance(self._rpc.super_address)
        rent = await self._rpc.get_rent_exempt_lamports()
        needed = payout.lamports + SWEEP_FEE_LAMPORTS + rent
        if super_balance < needed:
            raise WithdrawalError(
                "The super wallet does not have enough on-chain SOL to pay this withdrawal right now."
            )
        try:
            signature = await self._rpc.send_from_super(wallet, payout)
        except SolanaTransferUnconfirmedError as exc:
            raise WithdrawalError(
                "Solana still did not confirm the payout. The player's casino balance was not changed."
            ) from exc
        except Exception as exc:
            raise WithdrawalError(_user_send_error(exc)) from exc
        try:
            await self._transactions.attach_signature(tx.transaction_id, signature)
        except Exception:
            self._logger.exception("withdrawal_signature_attach_failed", txId=tx.transaction_id)
        self._logger.info(
            "withdrawal_completed_on_chain",
            amountLamports=payout.lamports,
            destination=wallet,
            txSignature=signature,
            userId=discord_id,
        )
        if self._on_sent is not None:
            try:
                await self._on_sent(discord_id, payout, wallet, signature)
            except Exception:
                self._logger.exception("withdrawal_alert_failed", userId=discord_id)
        refreshed = await self._users.get(discord_id) or user
        return WithdrawalResult(
            user=refreshed,
            amount=payout,
            destination=wallet,
            signature=signature,
        )

    async def _refund(self, discord_id: str, amount: SolAmount, usd_price, wallet: str) -> None:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self._uow.begin() as uow:
                    await self._balances.credit(
                        user_id=discord_id,
                        amount=amount,
                        entry_type=LedgerEntryType.REFUND,
                        transaction_type=TransactionType.REFUND,
                        price_usd=usd_price,
                        description=f"Refund failed withdrawal to {wallet}",
                        withdrawn_refund=amount,
                        session=uow.session,
                    )
                return
            except Exception as exc:
                last_error = exc
                self._logger.exception(
                    "withdrawal_refund_failed",
                    userId=discord_id,
                    amountLamports=amount.lamports,
                    attempt=attempt + 1,
                )
                await asyncio.sleep(0.5 * (attempt + 1))
        if last_error is not None:
            self._logger.error(
                "withdrawal_refund_gave_up",
                userId=discord_id,
                amountLamports=amount.lamports,
                error=str(last_error),
            )


def _destination_from_withdrawal(tx) -> str:
    metadata = tx.metadata if isinstance(getattr(tx, "metadata", None), dict) else {}
    dest = metadata.get("destination")
    if isinstance(dest, str) and dest.strip():
        return dest.strip()
    description = str(metadata.get("description") or "")
    prefix = "SOL withdrawal to "
    if description.startswith(prefix):
        wallet = description[len(prefix) :].strip()
        if wallet:
            return wallet
    raise WithdrawalError("Could not read the destination wallet from that withdrawal.")


def _user_send_error(exc: BaseException) -> str:
    detail = str(exc).lower()
    if "blockhash" in detail:
        return "Solana rejected the transaction. Your balance was refunded. Try again."
    if "insufficient" in detail or "no record of a prior credit" in detail:
        return "The super wallet could not pay this withdrawal. Your balance was refunded."
    if "rate" in detail or "too many" in detail or "forbidden" in detail or "method not found" in detail:
        return "The Solana RPC could not send the withdrawal. Your balance was refunded."
    return "The withdrawal could not be sent. Your balance was refunded."
