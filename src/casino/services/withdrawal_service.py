from __future__ import annotations

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
from casino.solana_client.rpc import SWEEP_FEE_LAMPORTS, SolanaRpcGateway
from casino.utils.money import SolAmount, subtract_transfer_fee, transfer_fee


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

        payout = subtract_transfer_fee(amount)
        if payout.is_zero():
            raise ValidationError("Enter a withdrawal amount greater than zero.")

        super_balance = await self._rpc.get_balance(self._rpc.super_address)
        rent = await self._rpc.get_rent_exempt_lamports()
        needed = payout.lamports + SWEEP_FEE_LAMPORTS + rent
        if super_balance < needed:
            raise WithdrawalError(
                "The house wallet does not have enough on-chain SOL to pay this withdrawal right now."
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
            except Exception as exc:
                self._logger.exception(
                    "withdrawal_send_failed",
                    destination=wallet,
                    amountLamports=payout.lamports,
                    error=str(exc),
                )
                await self._refund(discord_id, amount, usd_price, wallet)
                raise WithdrawalError(_user_send_error(exc)) from exc
            fee = transfer_fee(amount)
            if not fee.is_zero():
                try:
                    async with self._uow.begin() as uow:
                        await self._balances.credit_house_reserve(
                            amount=fee,
                            entry_type=LedgerEntryType.FEE,
                            transaction_type=TransactionType.FEE,
                            price_usd=usd_price,
                            description="Withdrawal transfer fee",
                            session=uow.session,
                        )
                except Exception:
                    self._logger.exception(
                        "withdrawal_fee_credit_failed",
                        amountLamports=fee.lamports,
                        txSignature=signature,
                    )
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

    async def _refund(self, discord_id: str, amount: SolAmount, usd_price, wallet: str) -> None:
        try:
            async with self._uow.begin() as uow:
                await self._balances.credit(
                    user_id=discord_id,
                    amount=amount,
                    entry_type=LedgerEntryType.REFUND,
                    transaction_type=TransactionType.REFUND,
                    price_usd=usd_price,
                    description=f"Refund failed withdrawal to {wallet}",
                    session=uow.session,
                )
        except Exception:
            self._logger.exception("withdrawal_refund_failed", userId=discord_id, amountLamports=amount.lamports)


def _user_send_error(exc: BaseException) -> str:
    detail = str(exc).lower()
    if "blockhash" in detail:
        return "Solana rejected the transaction. Your balance was refunded. Try again."
    if "insufficient" in detail or "no record of a prior credit" in detail:
        return "The house wallet could not pay this withdrawal. Your balance was refunded."
    if "rate" in detail or "too many" in detail or "forbidden" in detail or "method not found" in detail:
        return "The Solana RPC could not send the withdrawal. Your balance was refunded."
    return "The withdrawal could not be sent. Your balance was refunded."
