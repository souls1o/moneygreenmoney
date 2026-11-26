from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

from solana.rpc.commitment import Confirmed, Finalized, Processed

from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.enums import LedgerEntryType, TransactionType
from casino.domain.records import HOUSE_USER_ID, UserRecord
from casino.errors import AlreadyProcessedError, DepositError, FeatureDisabledError
from casino.logging.logger import LogContext, get_logger
from casino.repositories.transaction_repository import TransactionRepository
from casino.services.balance_service import BalanceService
from casino.services.custody_service import CustodyService
from casino.services.investor_service import InvestorService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.solana_client.deposit_parser import (
    IncompleteDepositTransaction,
    ParsedIncomingTransfer,
    SolanaDepositParser,
)
from casino.solana_client.rpc import SolanaRpcGateway
from casino.utils.text import TextNormalizer


class DepositTransactionPendingError(Exception):
    """The signature is known but RPC has not returned the transaction body yet."""


@dataclass(frozen=True, slots=True)
class DepositWatchTarget:
    address: str
    is_house: bool = False
    user_id: str | None = None


class DepositService:
    """Credit deposits by destination address. Player SOL sweeps to super; house SOL stays put."""

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
        house_repository=None,
        investors: InvestorService | None = None,
        custody: CustodyService | None = None,
    ) -> None:
        self._users = user_service
        self._balances = balance_service
        self._prices = price_service
        self._transactions = transaction_repository
        self._rpc = rpc
        self._parser = parser
        self._uow = unit_of_work
        self._normalizer = normalizer
        self._house = house_repository
        self._investors = investors
        self._custody = custody
        self._on_credited = None
        self._on_house_credited = None
        self._logger = get_logger("casino.deposit")
        self._sweep_tasks: set[asyncio.Task[None]] = set()

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

    async def ingest_signature(
        self,
        signature: str,
        target: DepositWatchTarget,
        payload: dict | None = None,
    ) -> UserRecord | None:
        existing = await self._transactions.find_by_signature(signature)
        if existing is not None:
            self._schedule_sweep(target)
            return None
        parsed = self._parse_incoming_payload(payload, signature, target.address) if payload else None
        if parsed is None:
            try:
                parsed = await self._incoming_or_pending(signature, target.address)
            except DepositTransactionPendingError:
                parsed = await self._incoming_from_balance_delta(signature, target.address)
                if parsed is None:
                    raise
        if parsed is None:
            parsed = await self._incoming_from_balance_delta(signature, target.address)
        if parsed is None:
            return None
        if target.is_house and self._is_internal_transfer(parsed):
            self._logger.info(
                "internal_transfer_ignored",
                txSignature=parsed.signature,
                source=parsed.source,
                destination=parsed.destination,
                amountLamports=parsed.lamports,
            )
            return None
        if target.is_house:
            await self.credit_house(parsed)
            return None
        user = await self._users.get_by_deposit_address(target.address)
        if user is None and target.user_id:
            user = await self._users.get(target.user_id)
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
        self._schedule_sweep(target)
        return credited

    async def credit_parsed(self, user: UserRecord, parsed: ParsedIncomingTransfer) -> UserRecord:
        existing = await self._transactions.find_by_signature(parsed.signature)
        if existing is not None:
            raise AlreadyProcessedError("That deposit was already credited.")
        if parsed.amount.is_zero():
            raise DepositError("The transfer amount is zero.")

        credited_amount = parsed.amount

        usd_price = Decimal("0")
        try:
            quote = await self._prices.get_quote(allow_stale=True)
            usd_price = quote.sol_price("USD")
        except Exception:
            self._logger.warning("deposit_credited_without_price", txSignature=parsed.signature)
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
                self._spawn(
                    self._notify_credited(mutation.user, parsed, usd_price),
                    "deposit_alert_failed",
                    parsed.signature,
                )
            return mutation.user

    async def credit_house(self, parsed: ParsedIncomingTransfer) -> None:
        existing = await self._transactions.find_by_signature(parsed.signature)
        if existing is not None:
            return
        if self._is_internal_transfer(parsed):
            self._logger.info(
                "internal_transfer_ignored",
                txSignature=parsed.signature,
                source=parsed.source,
                destination=parsed.destination,
                amountLamports=parsed.lamports,
            )
            return
        if parsed.amount.is_zero():
            raise DepositError("The transfer amount is zero.")
        credited_amount = parsed.amount
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=HOUSE_USER_ID, txSignature=parsed.signature):
            try:
                async with self._uow.begin() as uow:
                    book_before = 0
                    if self._house is not None:
                        book_before = (await self._house.get(session=uow.session)).sol_balance_lamports
                    await self._balances.credit_house_reserve(
                        amount=credited_amount,
                        entry_type=LedgerEntryType.DEPOSIT,
                        transaction_type=TransactionType.DEPOSIT,
                        price_usd=usd_price,
                        description=f"SOL house deposit {parsed.signature}",
                        solana_signature=parsed.signature,
                        session=uow.session,
                        align_wallet=False,
                    )
                    if self._investors is not None:
                        await self._investors.on_founder_inflow(
                            credited_amount, book_before=book_before, session=uow.session
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
                self._spawn(
                    self._notify_house_credited(parsed, usd_price),
                    "house_deposit_alert_failed",
                    parsed.signature,
                )

    async def sweep_user(self, discord_id: str) -> str | None:
        return await self._rpc.collect_user_into_super(discord_id)

    async def sweep_house(self) -> str | None:
        return await self.sync_house_wallet()

    async def sync_house_wallet(self) -> str | None:
        if self._custody is None:
            return None
        return await self._custody.sync_house_wallet()

    async def sweep_target(self, target: DepositWatchTarget) -> None:
        if target.is_house:
            return
        if target.user_id:
            await self.sweep_user(target.user_id)

    async def recover_swept_deposit(self, target: DepositWatchTarget) -> UserRecord | None:
        """Credit a player if SOL already left their deposit address for the super wallet."""
        if target.is_house or not target.user_id:
            return None
        super_addr = self.super_address()
        if not super_addr:
            return None
        try:
            signatures = await self._rpc.get_signatures(target.address, limit=25)
        except TypeError:
            signatures = await self._rpc.get_signatures(target.address)
        except Exception:
            self._logger.exception("recover_signatures_failed", address=target.address)
            return None
        if not signatures:
            return None
        user = await self._users.get_by_deposit_address(target.address) or await self._users.get(target.user_id)
        if user is None:
            return None
        leftover = 0
        try:
            leftover = int(await self._rpc.get_balance(target.address))
        except Exception:
            leftover = 0
        delta_fn = getattr(self._parser, "balance_delta", None)
        fetched = 0
        for signature in reversed(list(signatures)):
            try:
                existing = await self._transactions.find_by_signature(signature)
                if existing is not None:
                    continue
                if fetched >= 8:
                    break
                payload = await self._payload_for_signature(signature)
                fetched += 1
                if payload is None or delta_fn is None:
                    continue
                player_delta = delta_fn(payload, target.address)
                super_delta = delta_fn(payload, super_addr)
            except Exception:
                self._logger.exception("recover_signature_failed", txSignature=signature)
                continue
            if player_delta is not None and player_delta > 0:
                incoming = ParsedIncomingTransfer(
                    signature=signature,
                    lamports=player_delta,
                    destination=target.address,
                    source=None,
                    memo=None,
                )
                try:
                    credited = await self.credit_parsed(user, incoming)
                except AlreadyProcessedError:
                    return user
                except Exception:
                    self._logger.exception("recover_credit_incoming_failed", txSignature=signature)
                    continue
                self._logger.info(
                    "deposit_recovered_from_incoming",
                    txSignature=signature,
                    amountLamports=player_delta,
                    userId=user.discord_id,
                )
                self._schedule_sweep(target)
                return credited
            swept_lamports = 0
            if super_delta is not None and super_delta > 0 and (player_delta is None or player_delta < 0):
                swept_lamports = super_delta
            elif player_delta is not None and player_delta < 0:
                swept_lamports = -player_delta
            if swept_lamports <= 0:
                continue
            recovered = ParsedIncomingTransfer(
                signature=signature,
                lamports=max(int(swept_lamports) + max(leftover, 0), 0),
                destination=target.address,
                source=target.address,
                memo=None,
            )
            try:
                credited = await self.credit_parsed(user, recovered)
            except AlreadyProcessedError:
                return user
            except Exception:
                self._logger.exception("recover_credit_sweep_failed", txSignature=signature)
                continue
            self._logger.info(
                "deposit_recovered_from_sweep",
                txSignature=signature,
                amountLamports=recovered.lamports,
                userId=user.discord_id,
            )
            return credited
        return None

    async def reconcile_user(self, discord_id: str) -> UserRecord | None:
        user = await self._users.get(discord_id)
        if user is None or not user.deposit_address:
            return None
        target = DepositWatchTarget(address=user.deposit_address, user_id=discord_id)
        try:
            signatures = await self._rpc.get_signatures(target.address, limit=25)
        except TypeError:
            signatures = await self._rpc.get_signatures(target.address)
        except Exception as exc:
            self._logger.exception("reconcile_signatures_failed", userId=discord_id)
            raise DepositError(f"Could not load signatures for that deposit address: {exc}") from exc
        for signature in reversed(list(signatures)):
            try:
                credited = await self.ingest_signature(signature, target)
            except DepositTransactionPendingError:
                continue
            except Exception:
                self._logger.exception("reconcile_ingest_failed", txSignature=signature)
                continue
            if credited is not None:
                return credited
        return await self.recover_swept_deposit(target)

    def _parse_incoming_payload(
        self, payload: dict | None, signature: str, destination: str
    ) -> ParsedIncomingTransfer | None:
        if not isinstance(payload, dict):
            return None
        try:
            parsed = self._parser.parse(payload, destination=destination, signature=signature)
        except IncompleteDepositTransaction:
            parsed = None
        except Exception:
            parsed = None
        if parsed is not None:
            return parsed
        delta_fn = getattr(self._parser, "balance_delta", None)
        if delta_fn is None:
            return None
        try:
            delta = delta_fn(payload, destination)
        except Exception:
            return None
        if delta is None or int(delta) <= 0:
            return None
        return ParsedIncomingTransfer(
            signature=signature,
            lamports=int(delta),
            destination=destination,
            source=None,
            memo=None,
        )

    async def _incoming_from_balance_delta(
        self, signature: str, destination: str
    ) -> ParsedIncomingTransfer | None:
        delta_fn = getattr(self._parser, "balance_delta", None)
        if delta_fn is None:
            return None
        payload = await self._payload_for_signature(signature)
        if payload is None:
            return None
        try:
            delta = delta_fn(payload, destination)
        except Exception:
            return None
        if delta is None or int(delta) <= 0:
            return None
        return ParsedIncomingTransfer(
            signature=signature,
            lamports=int(delta),
            destination=destination,
            source=None,
            memo=None,
        )

    async def _payload_for_signature(self, signature: str) -> dict | None:
        for encoding in ("jsonParsed", "json"):
            payload = await self._fetch_parsed_transaction(signature, encoding=encoding)
            if payload is not None:
                return payload
        return None

    def _is_internal_transfer(self, parsed: ParsedIncomingTransfer) -> bool:
        source = (parsed.source or "").strip()
        if not source:
            return False
        owned = {addr for addr in (self._rpc.house_address, self._rpc.super_address) if addr}
        return source in owned

    async def parse_signature(self, signature: str, destination: str) -> ParsedIncomingTransfer | None:
        self.require_configured()
        payload = await self._fetch_parsed_transaction(signature)
        if payload is None:
            return None
        return self._parser.parse(payload, destination=destination, signature=signature)

    async def _incoming_or_pending(self, signature: str, destination: str) -> ParsedIncomingTransfer | None:
        last_incomplete: IncompleteDepositTransaction | None = None
        saw_payload = False
        for encoding in ("jsonParsed", "json"):
            payload = await self._fetch_parsed_transaction(signature, encoding=encoding)
            if payload is None:
                continue
            saw_payload = True
            try:
                parsed = self._parser.parse(payload, destination=destination, signature=signature)
            except IncompleteDepositTransaction as exc:
                last_incomplete = exc
                continue
            if parsed is not None:
                return parsed
        if last_incomplete is not None:
            self._logger.info("deposit_transaction_incomplete", txSignature=signature, address=destination)
            raise DepositTransactionPendingError(signature) from last_incomplete
        if not saw_payload:
            self._logger.info("deposit_transaction_pending", txSignature=signature, address=destination)
            raise DepositTransactionPendingError(signature)
        return None

    async def _fetch_parsed_transaction(self, signature: str, *, encoding: str = "jsonParsed") -> dict | None:
        payload = await self._rpc_parsed_transaction(signature, Confirmed, encoding)
        if payload is not None:
            return payload
        return await self._rpc_parsed_transaction(signature, Finalized, encoding)

    async def _rpc_parsed_transaction(self, signature: str, commitment, encoding: str) -> dict | None:
        try:
            return await self._rpc.get_parsed_transaction(
                signature, commitment=commitment, encoding=encoding
            )
        except TypeError:
            try:
                return await self._rpc.get_parsed_transaction(signature, commitment=commitment)
            except TypeError:
                try:
                    return await self._rpc.get_parsed_transaction(signature)
                except Exception:
                    return None
        except Exception:
            return None

    def _schedule_sweep(self, target: DepositWatchTarget) -> None:
        self._spawn(self._safe_sweep(target), "deposit_sweep_failed", target.address)

    async def _safe_sweep(self, target: DepositWatchTarget) -> None:
        await self.sweep_target(target)

    def _spawn(self, coro, log_event: str, detail: str) -> None:
        task = asyncio.create_task(self._run_background(coro, log_event, detail))
        self._sweep_tasks.add(task)
        task.add_done_callback(self._sweep_tasks.discard)

    async def _run_background(self, coro, log_event: str, detail: str) -> None:
        try:
            await coro
        except Exception:
            self._logger.exception(log_event, detail=detail)

    async def _notify_credited(self, user: UserRecord, parsed: ParsedIncomingTransfer, usd_price) -> None:
        assert self._on_credited is not None
        await self._on_credited(user, parsed, usd_price)

    async def _notify_house_credited(self, parsed: ParsedIncomingTransfer, usd_price) -> None:
        assert self._on_house_credited is not None
        await self._on_house_credited(parsed, usd_price)
