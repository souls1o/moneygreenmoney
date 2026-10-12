from __future__ import annotations

from dataclasses import dataclass

from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.enums import LedgerEntryType, TransactionType
from casino.domain.records import HOUSE_USER_ID, UserRecord
from casino.errors import AlreadyProcessedError, DepositError, FeatureDisabledError
from casino.logging.logger import LogContext, get_logger
from casino.repositories.transaction_repository import TransactionRepository
from casino.services.balance_service import BalanceService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.solana_client.deposit_parser import ParsedIncomingTransfer, SolanaDepositParser
from casino.solana_client.rpc import SolanaRpcGateway
from casino.utils.money import subtract_transfer_fee, transfer_fee
from casino.utils.text import TextNormalizer


@dataclass(frozen=True, slots=True)
class DepositWatchTarget:
    address: str
    is_house: bool = False
    user_id: str | None = None


class DepositService:
    """Credit deposits by destination address, then sweep SOL into the super wallet."""

    def __init__(
        self,
        user_service: UserService,
        balance_service: BalanceService,
        price_service: PriceService,
        transaction_repository: TransactionRepository,
        rpc: SolanaRpcGateway,
        parser: SolanaDepositParser,
        unit_of_work: UnitOfWorkFactory,
        normalizer: TextNormalizer,
    ) -> None:
        self._users = user_service
        self._balances = balance_service
        self._prices = price_service
        self._transactions = transaction_repository
        self._rpc = rpc
        self._parser = parser
        self._uow = unit_of_work
        self._normalizer = normalizer
        self._on_credited = None
        self._on_house_credited = None
        self._logger = get_logger("casino.deposit")

    def set_listeners(self, *, on_credited=None, on_house_credited=None) -> None:
        self._on_credited = on_credited
        self._on_house_credited = on_house_credited

    def is_configured(self) -> bool:
        return self._rpc.is_configured

    def require_configured(self) -> None:
        if not self._rpc.is_configured:
            raise FeatureDisabledError(
                "On-chain deposits are disabled until DEPOSIT_MASTER_SECRET and "
                "SUPER_WALLET_PRIVATE_KEY are set."
            )

    def house_address(self) -> str | None:
        return self._rpc.house_address

    def super_address(self) -> str | None:
        return self._rpc.super_address

    async def watch_targets(self) -> list[DepositWatchTarget]:
        targets: list[DepositWatchTarget] = []
        house = self._rpc.house_address
        if house:
            targets.append(DepositWatchTarget(address=house, is_house=True, user_id=HOUSE_USER_ID))
        for user_id, address in await self._users.list_deposit_wallets():
            if house and address == house:
                continue
            targets.append(DepositWatchTarget(address=address, user_id=user_id))
        return targets

    async def ingest_signature(self, signature: str, target: DepositWatchTarget) -> UserRecord | None:
        parsed = await self._load_incoming(signature, target.address)
        if parsed is None:
            return None
        if target.is_house:
            await self.credit_house(parsed)
            await self.sweep_house()
            return None
        user = await self._users.get_by_deposit_address(target.address)
        if user is None:
            self._logger.warning(
                "deposit_address_unmatched",
                address=target.address,
                txSignature=parsed.signature,
            )
            return None
        try:
            credited = await self.credit_parsed(user, parsed)
        except AlreadyProcessedError:
            credited = user
        await self.sweep_user(user.discord_id)
        return credited

    async def credit_parsed(self, user: UserRecord, parsed: ParsedIncomingTransfer) -> UserRecord:
        existing = await self._transactions.find_by_signature(parsed.signature)
        if existing is not None:
            raise AlreadyProcessedError("That deposit was already credited.")
        if parsed.amount.is_zero():
            raise DepositError("The transfer amount is zero.")

        credited_amount = subtract_transfer_fee(parsed.amount)
        fee = transfer_fee(parsed.amount)
        if credited_amount.is_zero():
            raise DepositError("The transfer amount is zero.")

        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=user.discord_id, txSignature=parsed.signature):
            try:
                async with self._uow.begin() as uow:
                    mutation = await self._balances.credit(
                        user_id=user.discord_id,
                        amount=credited_amount,
                        entry_type=LedgerEntryType.DEPOSIT,
                        transaction_type=TransactionType.DEPOSIT,
                        price_usd=usd_price,
                        description=f"SOL deposit {parsed.signature}",
                        solana_signature=parsed.signature,
                        deposited=True,
                        session=uow.session,
                    )
                    if not fee.is_zero():
                        await self._balances.credit_house_reserve(
                            amount=fee,
                            entry_type=LedgerEntryType.FEE,
                            transaction_type=TransactionType.FEE,
                            price_usd=usd_price,
                            description="Deposit transfer fee",
                            session=uow.session,
                        )
            except AlreadyProcessedError:
                self._logger.info("deposit_duplicate", txSignature=parsed.signature)
                raise
            self._logger.info(
                "deposit_credited",
                amountLamports=credited_amount.lamports,
                destination=parsed.destination,
                ledgerId=mutation.ledger_entry.entry_id,
            )
            if self._on_credited is not None:
                try:
                    await self._on_credited(mutation.user, parsed, usd_price)
                except Exception:
                    self._logger.exception("deposit_alert_failed", txSignature=parsed.signature)
            return mutation.user

    async def credit_house(self, parsed: ParsedIncomingTransfer) -> None:
        existing = await self._transactions.find_by_signature(parsed.signature)
        if existing is not None:
            return
        if parsed.amount.is_zero():
            raise DepositError("The transfer amount is zero.")
        credited_amount = parsed.amount
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=HOUSE_USER_ID, txSignature=parsed.signature):
            try:
                async with self._uow.begin() as uow:
                    await self._balances.credit_house_reserve(
                        amount=credited_amount,
                        entry_type=LedgerEntryType.DEPOSIT,
                        transaction_type=TransactionType.DEPOSIT,
                        price_usd=usd_price,
                        description=f"SOL house deposit {parsed.signature}",
                        solana_signature=parsed.signature,
                        session=uow.session,
                    )
            except AlreadyProcessedError:
                self._logger.info("house_deposit_duplicate", txSignature=parsed.signature)
                return
            self._logger.info(
                "house_deposit_credited",
                amountLamports=credited_amount.lamports,
                destination=parsed.destination,
            )
            if self._on_house_credited is not None:
                try:
                    await self._on_house_credited(parsed, usd_price)
                except Exception:
                    self._logger.exception("house_deposit_alert_failed", txSignature=parsed.signature)

    async def sweep_user(self, discord_id: str) -> str | None:
        if not self._rpc.is_configured:
            return None
        try:
            return await self._rpc.sweep_to_super(self._rpc.player_keypair(discord_id))
        except Exception:
            self._logger.exception("deposit_sweep_failed", userId=discord_id)
            return None

    async def sweep_house(self) -> str | None:
        if not self._rpc.has_house_wallet or self._rpc.super_address is None:
            return None
        try:
            return await self._rpc.sweep_to_super(self._rpc.house_keypair())
        except Exception:
            self._logger.exception("house_sweep_failed")
            return None

    async def parse_signature(self, signature: str, destination: str) -> ParsedIncomingTransfer | None:
        self.require_configured()
        return await self._load_incoming(signature, destination)

    async def _load_incoming(self, signature: str, destination: str) -> ParsedIncomingTransfer | None:
        payload = await self._rpc.get_parsed_transaction(signature)
        if payload is None:
            return None
        return self._parser.parse(
            payload,
            destination=destination,
            signature=signature,
        )
