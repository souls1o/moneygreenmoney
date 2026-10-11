from __future__ import annotations

from dataclasses import dataclass

from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.enums import LedgerEntryType, TransactionType, UserRole
from casino.domain.records import HOUSE_USER_ID, HouseTreasuryRecord, UserRecord
from casino.errors import ConfigurationError, InsufficientHouseReserveError, ValidationError, WithdrawalError
from casino.logging.logger import get_logger
from casino.repositories.house_repository import HouseRepository
from casino.repositories.transaction_repository import TransactionRepository
from casino.services.balance_service import BalanceService
from casino.services.game_service import GameService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.solana_client.addresses import SolanaAddressValidator
from casino.solana_client.rpc import SWEEP_FEE_LAMPORTS, SolanaRpcGateway
from casino.utils.money import SolAmount, subtract_transfer_fee


@dataclass(frozen=True, slots=True)
class HouseWithdrawalResult:
    amount: SolAmount
    destination: str
    signature: str


class AdminService:
    def __init__(
        self,
        user_service: UserService,
        balance_service: BalanceService,
        game_service: GameService,
        house_repository: HouseRepository,
        price_service: PriceService,
        unit_of_work: UnitOfWorkFactory,
        rpc: SolanaRpcGateway,
        address_validator: SolanaAddressValidator,
        transactions: TransactionRepository,
    ) -> None:
        self._users = user_service
        self._balances = balance_service
        self._games = game_service
        self._house = house_repository
        self._prices = price_service
        self._uow = unit_of_work
        self._rpc = rpc
        self._addresses = address_validator
        self._transactions = transactions
        self._logger = get_logger("casino.admin")

    async def adjust(
        self,
        *,
        target_discord_id: str,
        amount: SolAmount,
        credit: bool,
        reason: str,
        actor_id: str,
    ) -> UserRecord:
        await self._users.get_or_create(target_discord_id)
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        description = f"Admin adjustment by {actor_id}: {reason}"
        async with self._uow.begin() as uow:
            if credit:
                mutation = await self._balances.credit(
                    user_id=target_discord_id,
                    amount=amount,
                    entry_type=LedgerEntryType.ADJUSTMENT,
                    transaction_type=TransactionType.ADJUSTMENT,
                    price_usd=usd_price,
                    description=description,
                    session=uow.session,
                )
            else:
                mutation = await self._balances.debit(
                    user_id=target_discord_id,
                    amount=amount,
                    entry_type=LedgerEntryType.ADJUSTMENT,
                    transaction_type=TransactionType.ADJUSTMENT,
                    price_usd=usd_price,
                    description=description,
                    session=uow.session,
                )
        self._logger.info(
            "admin_adjustment",
            userId=target_discord_id,
            actorId=actor_id,
            credit=credit,
            amountLamports=amount.lamports,
        )
        return mutation.user

    async def grant_role(self, target_discord_id: str, role: UserRole) -> UserRecord:
        return await self._users.grant_role(target_discord_id, role)

    async def cancel_game(self, game_id: str, actor_id: str) -> None:
        await self._games.cancel_game(game_id, actor_id, admin=True)

    async def expire_games(self) -> int:
        expired = await self._games.expire_open_games()
        return len(expired)

    async def house_treasury(self) -> HouseTreasuryRecord:
        return await self._house.get()

    async def fund_house(self, amount: SolAmount, actor_id: str) -> HouseTreasuryRecord:
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        async with self._uow.begin() as uow:
            treasury, _entry = await self._balances.credit_house_reserve(
                amount=amount,
                entry_type=LedgerEntryType.ADJUSTMENT,
                transaction_type=TransactionType.ADJUSTMENT,
                price_usd=usd_price,
                description=f"Admin house funding by {actor_id}",
                session=uow.session,
            )
        self._logger.info(
            "house_funded",
            actorId=actor_id,
            amountLamports=amount.lamports,
            userId=HOUSE_USER_ID,
        )
        return treasury

    async def withdraw_house(
        self,
        amount: SolAmount,
        destination: str,
        actor_id: str,
    ) -> HouseWithdrawalResult:
        if amount.is_zero():
            raise ValidationError("Enter a withdrawal amount greater than zero.")
        if not self._rpc.super_address:
            raise ConfigurationError("House withdrawals need SUPER_WALLET_PRIVATE_KEY.")
        wallet_raw = destination.strip()
        if not wallet_raw:
            raise ValidationError("Enter a destination wallet.")
        wallet = self._addresses.parse(wallet_raw)
        if wallet == self._rpc.super_address:
            raise ValidationError("Choose a different destination wallet.")

        payout = subtract_transfer_fee(amount)
        if payout.is_zero():
            raise ValidationError("Enter a withdrawal amount greater than zero.")

        super_balance = await self._rpc.get_balance(self._rpc.super_address)
        rent = await self._rpc.get_rent_exempt_lamports()
        needed = payout.lamports + SWEEP_FEE_LAMPORTS + rent
        if super_balance < needed:
            raise WithdrawalError(
                "The super wallet does not have enough on-chain SOL to pay this house withdrawal right now."
            )

        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        try:
            async with self._uow.begin() as uow:
                _treasury, entry = await self._balances.debit_house_reserve(
                    amount=amount,
                    price_usd=usd_price,
                    description=f"House withdrawal by {actor_id} to {wallet}",
                    entry_type=LedgerEntryType.WITHDRAWAL,
                    transaction_type=TransactionType.WITHDRAWAL,
                    session=uow.session,
                )
        except InsufficientHouseReserveError as exc:
            raise WithdrawalError("The house book does not have enough SOL for this withdrawal.") from exc

        try:
            signature = await self._rpc.send_from_super(wallet, payout)
        except Exception as exc:
            self._logger.exception(
                "house_withdrawal_send_failed",
                destination=wallet,
                amountLamports=payout.lamports,
                actorId=actor_id,
                error=str(exc),
            )
            await self._refund_house(amount, usd_price, wallet, actor_id)
            raise WithdrawalError(_house_send_error(exc)) from exc

        if entry.related_tx_id:
            try:
                await self._transactions.attach_signature(entry.related_tx_id, signature)
            except Exception:
                self._logger.exception("house_withdrawal_signature_attach_failed", txId=entry.related_tx_id)
        self._logger.info(
            "house_withdrawal_sent",
            actorId=actor_id,
            amountLamports=payout.lamports,
            destination=wallet,
            txSignature=signature,
            userId=HOUSE_USER_ID,
        )
        return HouseWithdrawalResult(amount=payout, destination=wallet, signature=signature)

    async def _refund_house(self, amount: SolAmount, usd_price, wallet: str, actor_id: str) -> None:
        try:
            async with self._uow.begin() as uow:
                await self._balances.credit_house_reserve(
                    amount=amount,
                    entry_type=LedgerEntryType.REFUND,
                    transaction_type=TransactionType.REFUND,
                    price_usd=usd_price,
                    description=f"Refund failed house withdrawal by {actor_id} to {wallet}",
                    session=uow.session,
                )
        except Exception:
            self._logger.exception(
                "house_withdrawal_refund_failed",
                actorId=actor_id,
                amountLamports=amount.lamports,
            )


def _house_send_error(exc: BaseException) -> str:
    detail = str(exc).lower()
    if "blockhash" in detail:
        return "Solana rejected the transaction. House reserves were refunded. Try again."
    if "insufficient" in detail or "no record of a prior credit" in detail:
        return "The super wallet could not pay this house withdrawal. House reserves were refunded."
    if "rate" in detail or "too many" in detail or "forbidden" in detail or "method not found" in detail:
        return "The Solana RPC could not send the house withdrawal. House reserves were refunded."
    return "The house withdrawal could not be sent. House reserves were refunded."
