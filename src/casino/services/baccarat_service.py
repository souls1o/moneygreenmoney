from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from casino.config.settings import Settings
from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.baccarat import (
    BACCARAT_REVEAL_SECONDS,
    BACCARAT_SECONDS,
    BaccaratBetResult,
    BaccaratDeal,
    BaccaratSide,
    payout_lamports,
    profit_lamports,
)
from casino.domain.enums import BaccaratStatus, LedgerEntryType, TransactionType
from casino.domain.records import BaccaratRecord
from casino.errors import BaccaratError, InsufficientHouseReserveError, NotFoundError
from casino.logging.logger import LogContext, get_logger
from casino.repositories.baccarat_repository import BaccaratRepository
from casino.services.balance_service import BalanceService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.clock import Clock
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService, SolAmount, house_table_cap_lamports
from casino.utils.rng import ProvablyFairService, combined_client_seed


class BaccaratService:
    def __init__(
        self,
        baccarat_repository: BaccaratRepository,
        user_service: UserService,
        user_repository,
        balance_service: BalanceService,
        price_service: PriceService,
        validation: ValidationService,
        math_service: MathService,
        rng: ProvablyFairService,
        identifiers: IdentifierFactory,
        clock: Clock,
        unit_of_work: UnitOfWorkFactory,
        settings: Settings,
        house_repository,
    ) -> None:
        self._tables = baccarat_repository
        self._users = user_service
        self._user_repo = user_repository
        self._balances = balance_service
        self._prices = price_service
        self._validation = validation
        self._math = math_service
        self._rng = rng
        self._identifiers = identifiers
        self._clock = clock
        self._uow = unit_of_work
        self._settings = settings
        self._house = house_repository
        self._logger = get_logger("casino.baccarat")

    async def get(self, baccarat_id: str) -> BaccaratRecord:
        table = await self._tables.find_by_id(baccarat_id)
        if table is None:
            raise NotFoundError("That baccarat table was not found.")
        return table

    async def live_for_guild(self, guild_id: str) -> BaccaratRecord | None:
        return await self._tables.find_live(guild_id)

    async def list_live(self) -> list[BaccaratRecord]:
        return await self._tables.list_live()

    async def attach_listing(self, baccarat_id: str, channel_id: str, message_id: str) -> None:
        await self._tables.set_listing_ref(baccarat_id, channel_id, message_id)

    async def attach_history(self, baccarat_id: str, message_id: str) -> None:
        await self._tables.set_history_ref(baccarat_id, message_id)

    async def ensure_open(
        self,
        guild_id: str,
        *,
        channel_id: str | None = None,
        message_id: str | None = None,
    ) -> BaccaratRecord:
        existing = await self._tables.find_live(guild_id)
        if existing is not None:
            if channel_id and message_id and (
                existing.channel_id != channel_id or existing.message_id != message_id
            ):
                await self._tables.set_listing_ref(existing.baccarat_id, channel_id, message_id)
                existing.channel_id = channel_id
                existing.message_id = message_id
            return existing
        now = self._clock.now()
        commitment = self._rng.commit()
        table = BaccaratRecord(
            baccarat_id=self._identifiers.baccarat_id(),
            status=BaccaratStatus.OPEN,
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            server_seed_hash=commitment.server_seed_hash,
            server_seed=commitment.server_seed,
            client_seed="",
            nonce=await self._house.take_next_fair_nonce(),
            created_at=now,
        )
        await self._tables.insert(table)
        self._logger.info("baccarat_opened", baccaratId=table.baccarat_id, guildId=guild_id)
        return table

    async def place_bet(
        self,
        baccarat_id: str,
        player_id: str,
        side: BaccaratSide,
        fiat_amount: Decimal,
    ) -> BaccaratRecord:
        user = await self._users.get_or_create(player_id)
        quote = await self._prices.get_quote()
        currency_price = quote.sol_price(user.display_currency)
        usd_price = quote.sol_price("USD")
        amount_usd = fiat_amount * usd_price / currency_price
        self._validation.validate_bet(amount_usd)
        bet = self._math.fiat_to_sol(fiat_amount, currency_price)
        if bet.is_zero():
            raise BaccaratError("Bet more than zero.")
        profit = SolAmount.from_lamports(profit_lamports(bet.lamports, side))
        now = self._clock.now()
        with LogContext.bind(userId=player_id, baccaratId=baccarat_id):
            async with self._uow.begin() as uow:
                current = await self._tables.find_by_id(baccarat_id, session=uow.session)
                if current is None:
                    raise NotFoundError("That baccarat table was not found.")
                if not current.is_open():
                    raise BaccaratError("This round is not accepting bets.")
                if current.expires_at is not None and current.expires_at <= now:
                    raise BaccaratError("Betting has closed for this round.")
                if current.has_bet(player_id):
                    raise BaccaratError("You already have a bet this round.")
                await self._require_house_cap(current.reserved_total(), profit.lamports)
                debit = await self._balances.debit(
                    user_id=player_id,
                    amount=bet,
                    entry_type=LedgerEntryType.BET,
                    transaction_type=TransactionType.BET,
                    price_usd=usd_price,
                    description=f"Baccarat {side.label()} {baccarat_id}",
                    game_id=baccarat_id,
                    session=uow.session,
                )
                current.ledger_ids.append(debit.ledger_entry.entry_id)
                try:
                    _treasury, house_bet = await self._balances.debit_house_reserve(
                        amount=profit,
                        price_usd=usd_price,
                        description=f"House baccarat {baccarat_id}",
                        game_id=baccarat_id,
                        session=uow.session,
                    )
                except InsufficientHouseReserveError as exc:
                    raise BaccaratError(
                        "The house book does not have enough SOL for that bet."
                    ) from exc
                current.ledger_ids.append(house_bet.entry_id)
                current.bets_lamports[player_id] = bet.lamports
                current.bets_sides[player_id] = side.value
                current.reserved_lamports[player_id] = profit.lamports
                fair = await self._users.ensure_fairness(player_id, session=uow.session)
                if fair.fair_client_seed:
                    current.client_seeds[player_id] = fair.fair_client_seed
                    current.client_seed = combined_client_seed(
                        current.client_seeds, list(current.bets_lamports)
                    )
                if current.opener_id is None:
                    current.opener_id = player_id
                    current.expires_at = now + timedelta(seconds=BACCARAT_SECONDS)
                replaced = await self._tables.replace(current, session=uow.session)
                if not replaced:
                    raise BaccaratError("That table changed. Try again.")
            self._logger.info(
                "baccarat_bet",
                amountLamports=bet.lamports,
                side=side.value,
                players=len(current.bets_lamports),
            )
        return current

    async def settle_due(self) -> list[BaccaratRecord]:
        settled: list[BaccaratRecord] = []
        for table in await self._tables.list_due(self._clock.now()):
            try:
                settled.append(await self.settle(table.baccarat_id))
            except Exception:
                self._logger.exception("baccarat_settle_failed", baccaratId=table.baccarat_id)
        return settled

    async def reset_due(self) -> list[BaccaratRecord]:
        opened: list[BaccaratRecord] = []
        for table in await self._tables.list_reveal_due(self._clock.now()):
            try:
                opened.append(await self.reset_after_reveal(table.baccarat_id))
            except Exception:
                self._logger.exception("baccarat_reset_failed", baccaratId=table.baccarat_id)
        return opened

    async def settle(self, baccarat_id: str) -> BaccaratRecord:
        now = self._clock.now()
        existing = await self.get(baccarat_id)
        if existing.status in {
            BaccaratStatus.SHOWING,
            BaccaratStatus.COMPLETED,
            BaccaratStatus.REFUNDED,
        }:
            return existing
        if existing.status is BaccaratStatus.OPEN:
            if existing.expires_at is None or existing.expires_at > now:
                raise BaccaratError("This round is still open.")
            locked = await self._tables.try_begin_settle(baccarat_id, now)
            if locked is None:
                current = await self.get(baccarat_id)
                if current.status in {
                    BaccaratStatus.SHOWING,
                    BaccaratStatus.COMPLETED,
                    BaccaratStatus.REFUNDED,
                    BaccaratStatus.SETTLING,
                }:
                    if current.status is not BaccaratStatus.SETTLING:
                        return current
                    existing = current
                else:
                    raise BaccaratError("This round is still open.")
            else:
                existing = locked
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(baccaratId=baccarat_id):
            async with self._uow.begin() as uow:
                current = await self._tables.find_by_id(baccarat_id, session=uow.session)
                if current is None:
                    raise NotFoundError("That baccarat table was not found.")
                if current.status in {
                    BaccaratStatus.SHOWING,
                    BaccaratStatus.COMPLETED,
                    BaccaratStatus.REFUNDED,
                }:
                    return current
                if not current.bets_lamports:
                    current.status = BaccaratStatus.SHOWING
                    current.reveal_until = now + timedelta(seconds=BACCARAT_REVEAL_SECONDS)
                else:
                    if not current.server_seed:
                        raise BaccaratError("This table is missing its server seed.")
                    fair = self._rng.resolve_baccarat(
                        current.server_seed,
                        current.client_seed,
                        current.nonce,
                    )
                    await self._pay_round(current, fair.deal, usd_price, now, session=uow.session)
                    current.fair_digest = fair.digest
                    current.player_cards = list(fair.deal.player_cards)
                    current.banker_cards = list(fair.deal.banker_cards)
                    current.outcome = fair.deal.outcome
                    current.status = BaccaratStatus.SHOWING
                    current.reveal_until = now + timedelta(seconds=BACCARAT_REVEAL_SECONDS)
                current.resolved_at = now
                replaced = await self._tables.replace(current, session=uow.session)
                if not replaced:
                    raise BaccaratError("The table could not be settled.")
            self._logger.info(
                "baccarat_settled",
                status=current.status.value,
                outcome=current.outcome.value if current.outcome else None,
                potLamports=current.pot_lamports(),
            )
        return current

    async def reset_after_reveal(self, baccarat_id: str) -> BaccaratRecord:
        now = self._clock.now()
        current = await self.get(baccarat_id)
        live = await self._tables.find_live(current.guild_id)
        if live is not None and live.baccarat_id != current.baccarat_id:
            return live
        if current.status is BaccaratStatus.OPEN:
            return current
        if current.reveal_until is not None and current.reveal_until > now:
            return current
        if current.status is BaccaratStatus.SHOWING:
            current.status = BaccaratStatus.COMPLETED
            await self._tables.replace(current)
        return await self.ensure_open(
            current.guild_id,
            channel_id=current.channel_id,
            message_id=current.message_id,
        )

    async def _pay_round(
        self,
        table: BaccaratRecord,
        deal: BaccaratDeal,
        usd_price: Decimal,
        now,
        *,
        session,
    ) -> None:
        house_pnl = 0
        outcome = deal.outcome
        for player_id, lamports in table.bets_lamports.items():
            side = BaccaratSide(table.bets_sides[player_id])
            result, payout = payout_lamports(lamports, side, outcome)
            reserved = int(table.reserved_lamports.get(player_id, 0))
            table.payouts_lamports[player_id] = payout
            if result is BaccaratBetResult.WIN:
                credit = await self._balances.credit(
                    user_id=player_id,
                    amount=SolAmount.from_lamports(payout),
                    entry_type=LedgerEntryType.WIN,
                    transaction_type=TransactionType.WIN,
                    price_usd=usd_price,
                    description=f"Baccarat win {table.baccarat_id}",
                    game_id=table.baccarat_id,
                    won=SolAmount.from_lamports(payout),
                    session=session,
                )
                table.ledger_ids.append(credit.ledger_entry.entry_id)
                house_pnl -= payout - lamports
            elif result is BaccaratBetResult.PUSH:
                refund = await self._balances.credit(
                    user_id=player_id,
                    amount=SolAmount.from_lamports(payout),
                    entry_type=LedgerEntryType.REFUND,
                    transaction_type=TransactionType.REFUND,
                    price_usd=usd_price,
                    description=f"Baccarat push {table.baccarat_id}",
                    game_id=table.baccarat_id,
                    session=session,
                )
                table.ledger_ids.append(refund.ledger_entry.entry_id)
                leftover = SolAmount.from_lamports(reserved)
                if not leftover.is_zero():
                    _treasury, house_back = await self._balances.credit_house_reserve(
                        amount=leftover,
                        entry_type=LedgerEntryType.REFUND,
                        transaction_type=TransactionType.REFUND,
                        price_usd=usd_price,
                        description=f"Baccarat house push {table.baccarat_id}",
                        game_id=table.baccarat_id,
                        session=session,
                    )
                    table.ledger_ids.append(house_back.entry_id)
            else:
                leftover = SolAmount.from_lamports(reserved + lamports)
                _treasury, take = await self._balances.credit_house_reserve(
                    amount=leftover,
                    entry_type=LedgerEntryType.WIN,
                    transaction_type=TransactionType.WIN,
                    price_usd=usd_price,
                    description=f"Baccarat house {table.baccarat_id}",
                    game_id=table.baccarat_id,
                    session=session,
                )
                table.ledger_ids.append(take.entry_id)
                house_pnl += lamports
            wager = SolAmount.from_lamports(lamports)
            wager_usd = self._math.sol_to_fiat(wager, usd_price)
            won = result is BaccaratBetResult.WIN
            payout_amount = SolAmount.from_lamports(payout)
            payout_usd = self._math.sol_to_fiat(payout_amount, usd_price) if won else Decimal("0")
            await self._user_repo.apply_game_stats(
                player_id,
                wagered=wager,
                wagered_usd=wager_usd,
                max_bet_usd=wager_usd,
                fees_paid=SolAmount.zero(),
                won=won,
                won_usd=payout_usd,
                now=now,
                session=session,
            )
        table.house_pnl_lamports = house_pnl

    async def _require_house_cap(self, reserved_lamports: int, extra_lamports: int) -> None:
        treasury = await self._house.get()
        cap = house_table_cap_lamports(
            treasury.sol_balance_lamports,
            reserved_lamports=reserved_lamports,
        )
        if reserved_lamports + extra_lamports <= cap:
            return
        raise BaccaratError("The house can take at most 10% of the house book on this table.")
