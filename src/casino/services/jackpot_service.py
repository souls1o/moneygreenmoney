from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from casino.config.settings import Settings
from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.enums import JackpotStatus, LedgerEntryType, TransactionType
from casino.domain.jackpot import (
    JACKPOT_REVEAL_SECONDS,
    JACKPOT_SECONDS,
    require_join_amount,
)
from casino.domain.records import JackpotRecord
from casino.errors import JackpotError, NotFoundError
from casino.logging.logger import LogContext, get_logger
from casino.repositories.jackpot_repository import JackpotRepository
from casino.services.balance_service import BalanceService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.clock import Clock
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService, SolAmount
from casino.utils.rng import ProvablyFairService


class JackpotService:
    def __init__(
        self,
        jackpot_repository: JackpotRepository,
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
        self._jackpots = jackpot_repository
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
        self._logger = get_logger("casino.jackpot")

    async def get(self, jackpot_id: str) -> JackpotRecord:
        jackpot = await self._jackpots.find_by_id(jackpot_id)
        if jackpot is None:
            raise NotFoundError("That jackpot was not found.")
        return jackpot

    async def live_for_guild(self, guild_id: str) -> JackpotRecord | None:
        return await self._jackpots.find_live(guild_id)

    async def list_live(self) -> list[JackpotRecord]:
        return await self._jackpots.list_live()

    async def attach_listing(self, jackpot_id: str, channel_id: str, message_id: str) -> None:
        await self._jackpots.set_listing_ref(jackpot_id, channel_id, message_id)

    async def ensure_open(
        self,
        guild_id: str,
        *,
        channel_id: str | None = None,
        message_id: str | None = None,
    ) -> JackpotRecord:
        existing = await self._jackpots.find_live(guild_id)
        if existing is not None:
            if channel_id and message_id and (
                existing.channel_id != channel_id or existing.message_id != message_id
            ):
                await self._jackpots.set_listing_ref(existing.jackpot_id, channel_id, message_id)
                existing.channel_id = channel_id
                existing.message_id = message_id
            return existing
        now = self._clock.now()
        commitment = self._rng.commit()
        jackpot = JackpotRecord(
            jackpot_id=self._identifiers.jackpot_id(),
            status=JackpotStatus.OPEN,
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            server_seed_hash=commitment.server_seed_hash,
            server_seed=commitment.server_seed,
            client_seed=self._rng.client_seed(),
            nonce=await self._house.take_next_fair_nonce(),
            created_at=now,
        )
        await self._jackpots.insert(jackpot)
        self._logger.info("jackpot_opened", jackpotId=jackpot.jackpot_id, guildId=guild_id)
        return jackpot

    async def join(self, jackpot_id: str, player_id: str, fiat_amount: Decimal) -> JackpotRecord:
        user = await self._users.get_or_create(player_id)
        quote = await self._prices.get_quote()
        currency_price = quote.sol_price(user.display_currency)
        usd_price = quote.sol_price("USD")
        amount_usd = fiat_amount * usd_price / currency_price
        self._validation.validate_bet(amount_usd)
        bet = self._math.fiat_to_sol(fiat_amount, currency_price)
        if bet.is_zero():
            raise JackpotError("Join with more than zero.")
        now = self._clock.now()
        with LogContext.bind(userId=player_id, jackpotId=jackpot_id):
            async with self._uow.begin() as uow:
                current = await self._jackpots.find_by_id(jackpot_id, session=uow.session)
                if current is None:
                    raise NotFoundError("That jackpot was not found.")
                if not current.is_open():
                    raise JackpotError("This jackpot is not accepting joins.")
                if current.has_joined(player_id):
                    raise JackpotError("You already joined this jackpot.")
                require_join_amount(current.opener_lamports(), bet.lamports)
                debit = await self._balances.debit(
                    user_id=player_id,
                    amount=bet,
                    entry_type=LedgerEntryType.BET,
                    transaction_type=TransactionType.BET,
                    price_usd=usd_price,
                    description=f"Jackpot join {jackpot_id}",
                    game_id=jackpot_id,
                    session=uow.session,
                )
                current.ledger_ids.append(debit.ledger_entry.entry_id)
                current.entries_lamports[player_id] = bet.lamports
                if current.opener_id is None:
                    current.opener_id = player_id
                    current.expires_at = now + timedelta(seconds=JACKPOT_SECONDS)
                replaced = await self._jackpots.replace(current, session=uow.session)
                if not replaced:
                    raise JackpotError("That jackpot changed. Try again.")
            self._logger.info(
                "jackpot_joined",
                amountLamports=bet.lamports,
                players=len(current.entries_lamports),
            )
        return current

    async def settle_due(self) -> list[JackpotRecord]:
        settled: list[JackpotRecord] = []
        for jackpot in await self._jackpots.list_due(self._clock.now()):
            try:
                settled.append(await self.settle(jackpot.jackpot_id))
            except Exception:
                self._logger.exception("jackpot_settle_failed", jackpotId=jackpot.jackpot_id)
        return settled

    async def reset_due(self) -> list[JackpotRecord]:
        opened: list[JackpotRecord] = []
        for jackpot in await self._jackpots.list_reveal_due(self._clock.now()):
            try:
                opened.append(await self.reset_after_reveal(jackpot.jackpot_id))
            except Exception:
                self._logger.exception("jackpot_reset_failed", jackpotId=jackpot.jackpot_id)
        return opened

    async def settle(self, jackpot_id: str) -> JackpotRecord:
        now = self._clock.now()
        existing = await self.get(jackpot_id)
        if existing.status in {
            JackpotStatus.SHOWING,
            JackpotStatus.COMPLETED,
            JackpotStatus.REFUNDED,
        }:
            return existing
        if existing.status is JackpotStatus.OPEN:
            if existing.expires_at is None or existing.expires_at > now:
                raise JackpotError("This jackpot is still open.")
            locked = await self._jackpots.try_begin_settle(jackpot_id, now)
            if locked is None:
                current = await self.get(jackpot_id)
                if current.status in {
                    JackpotStatus.SHOWING,
                    JackpotStatus.COMPLETED,
                    JackpotStatus.REFUNDED,
                    JackpotStatus.SETTLING,
                }:
                    if current.status is not JackpotStatus.SETTLING:
                        return current
                    existing = current
                else:
                    raise JackpotError("This jackpot is still open.")
            else:
                existing = locked
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(jackpotId=jackpot_id):
            async with self._uow.begin() as uow:
                current = await self._jackpots.find_by_id(jackpot_id, session=uow.session)
                if current is None:
                    raise NotFoundError("That jackpot was not found.")
                if current.status in {
                    JackpotStatus.SHOWING,
                    JackpotStatus.COMPLETED,
                    JackpotStatus.REFUNDED,
                }:
                    return current
                if len(current.entries_lamports) < 2:
                    await self._refund_all(current, usd_price, session=uow.session)
                    current.status = JackpotStatus.SHOWING
                    current.reveal_until = now + timedelta(seconds=JACKPOT_REVEAL_SECONDS)
                else:
                    if not current.server_seed:
                        raise JackpotError("This jackpot is missing its server seed.")
                    drawn = self._rng.resolve_jackpot(
                        current.server_seed,
                        current.client_seed,
                        current.nonce,
                        current.entries_lamports,
                    )
                    pot = SolAmount.from_lamports(current.pot_lamports())
                    split = self._math.split_pot(pot)
                    current.ticket = drawn.ticket
                    current.fair_digest = drawn.digest
                    current.winner_id = drawn.winner_id
                    current.house_fee_lamports = split.house_fee.lamports
                    current.winner_payout_lamports = split.winner_payout.lamports
                    win = await self._balances.credit(
                        user_id=drawn.winner_id,
                        amount=split.winner_payout,
                        entry_type=LedgerEntryType.WIN,
                        transaction_type=TransactionType.WIN,
                        price_usd=usd_price,
                        description=f"Jackpot win {jackpot_id}",
                        game_id=jackpot_id,
                        won=split.winner_payout,
                        session=uow.session,
                    )
                    current.ledger_ids.append(win.ledger_entry.entry_id)
                    if not split.house_fee.is_zero():
                        fee = await self._balances.credit_house_fee(
                            amount=split.house_fee,
                            price_usd=usd_price,
                            game_id=jackpot_id,
                            session=uow.session,
                        )
                        current.ledger_ids.append(fee.entry_id)
                    await self._apply_stats(current, split.house_fee, usd_price, now, session=uow.session)
                    current.status = JackpotStatus.SHOWING
                    current.reveal_until = now + timedelta(seconds=JACKPOT_REVEAL_SECONDS)
                current.resolved_at = now
                replaced = await self._jackpots.replace(current, session=uow.session)
                if not replaced:
                    raise JackpotError("The jackpot could not be settled.")
            self._logger.info(
                "jackpot_settled",
                status=current.status.value,
                winnerId=current.winner_id,
                potLamports=current.pot_lamports(),
            )
        return current

    async def reset_after_reveal(self, jackpot_id: str) -> JackpotRecord:
        now = self._clock.now()
        current = await self.get(jackpot_id)
        live = await self._jackpots.find_live(current.guild_id)
        if live is not None and live.jackpot_id != current.jackpot_id:
            return live
        if current.status is JackpotStatus.OPEN:
            return current
        if current.reveal_until is not None and current.reveal_until > now:
            return current
        if current.status is JackpotStatus.SHOWING:
            current.status = JackpotStatus.COMPLETED
            await self._jackpots.replace(current)
        return await self.ensure_open(
            current.guild_id,
            channel_id=current.channel_id,
            message_id=current.message_id,
        )

    async def _refund_all(self, jackpot: JackpotRecord, usd_price: Decimal, *, session) -> None:
        for player_id, lamports in jackpot.entries_lamports.items():
            amount = SolAmount.from_lamports(lamports)
            if amount.is_zero():
                continue
            mutation = await self._balances.credit(
                user_id=player_id,
                amount=amount,
                entry_type=LedgerEntryType.REFUND,
                transaction_type=TransactionType.REFUND,
                price_usd=usd_price,
                description=f"Jackpot refund {jackpot.jackpot_id}",
                game_id=jackpot.jackpot_id,
                session=session,
            )
            jackpot.ledger_ids.append(mutation.ledger_entry.entry_id)

    async def _apply_stats(
        self,
        jackpot: JackpotRecord,
        fees: SolAmount,
        usd_price: Decimal,
        now,
        *,
        session,
    ) -> None:
        for player_id, lamports in jackpot.entries_lamports.items():
            wager = SolAmount.from_lamports(lamports)
            wager_usd = self._math.sol_to_fiat(wager, usd_price)
            won = player_id == jackpot.winner_id
            payout = SolAmount.from_lamports(jackpot.winner_payout_lamports) if won else SolAmount.zero()
            payout_usd = self._math.sol_to_fiat(payout, usd_price) if won else Decimal("0")
            share_fee = fees if won else SolAmount.zero()
            await self._user_repo.apply_game_stats(
                player_id,
                wagered=wager,
                wagered_usd=wager_usd,
                max_bet_usd=wager_usd,
                fees_paid=share_fee,
                won=won,
                won_usd=payout_usd,
                now=now,
                session=session,
            )
