from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from casino.config.settings import Settings
from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.enums import LedgerEntryType, RainStatus, TransactionType
from casino.domain.records import RainRecord
from casino.errors import NotFoundError, RainError, ValidationError
from casino.logging.logger import LogContext, get_logger
from casino.repositories.rain_repository import RainRepository
from casino.services.balance_service import BalanceService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.clock import Clock
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService, SolAmount
from casino.utils.rng import ProvablyFairService


class RainService:
    """Player-funded rains. Joiners split the pot equally after the timer ends."""

    def __init__(
        self,
        rain_repository: RainRepository,
        user_service: UserService,
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
        self._rains = rain_repository
        self._users = user_service
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
        self._logger = get_logger("casino.rain")

    async def get(self, rain_id: str) -> RainRecord:
        rain = await self._rains.find_by_id(rain_id)
        if rain is None:
            raise NotFoundError("That rain was not found.")
        return rain

    async def create_rain(
        self,
        *,
        creator_id: str,
        fiat_amount: Decimal,
        duration_seconds: int,
        guild_id: str | None = None,
    ) -> RainRecord:
        self._require_duration(duration_seconds)
        user = await self._users.get_or_create(creator_id)
        quote = await self._prices.get_quote()
        currency = user.display_currency
        sol_price = quote.sol_price(currency)
        usd_price = quote.sol_price("USD")
        amount_usd = fiat_amount * usd_price / sol_price
        minimum = Decimal(self._settings.min_rain_usd)
        if amount_usd < minimum:
            raise ValidationError(
                f"Minimum rain is {self._math.format_fiat_symbol(minimum, 'USD')}."
            )
        pot = self._math.fiat_to_sol(fiat_amount, sol_price)
        now = self._clock.now()
        commitment = self._rng.commit()
        rain = RainRecord(
            rain_id=self._identifiers.rain_id(),
            status=RainStatus.PENDING,
            creator_id=creator_id,
            amount_lamports=pot.lamports,
            currency=currency,
            amount_fiat=fiat_amount,
            guild_id=guild_id,
            server_seed_hash=commitment.server_seed_hash,
            server_seed=commitment.server_seed,
            client_seed=self._rng.client_seed(),
            nonce=await self._house.take_next_fair_nonce(),
            created_at=now,
            expires_at=now + timedelta(seconds=duration_seconds),
        )
        with LogContext.bind(userId=creator_id, rainId=rain.rain_id):
            async with self._uow.begin() as uow:
                await self._rains.insert(rain, session=uow.session)
                mutation = await self._balances.debit(
                    user_id=creator_id,
                    amount=pot,
                    entry_type=LedgerEntryType.RAIN,
                    transaction_type=TransactionType.RAIN,
                    price_usd=usd_price,
                    description=f"Rain pot {rain.rain_id}",
                    game_id=rain.rain_id,
                    session=uow.session,
                )
                rain.ledger_ids.append(mutation.ledger_entry.entry_id)
                replaced = await self._rains.replace(rain, session=uow.session)
                if not replaced:
                    raise RainError("The rain could not be stored.")
            self._logger.info(
                "rain_created",
                amountLamports=pot.lamports,
                amountUsd=str(amount_usd),
                durationSeconds=duration_seconds,
            )
        return rain

    async def join_rain(self, rain_id: str, player_id: str) -> RainRecord:
        await self._users.get_or_create(player_id)
        existing = await self.get(rain_id)
        if existing.creator_id == player_id:
            raise RainError("You cannot join your own rain.")
        if existing.has_joined(player_id):
            raise RainError("You already joined this rain.")
        if not existing.is_open():
            raise RainError("This rain is no longer open.")
        now = self._clock.now()
        if existing.expires_at is not None and existing.expires_at <= now:
            raise RainError("This rain has already ended.")
        with LogContext.bind(userId=player_id, rainId=rain_id):
            claimed = await self._rains.try_add_joiner(rain_id, player_id, now)
            if claimed is None:
                current = await self.get(rain_id)
                if current.creator_id == player_id:
                    raise RainError("You cannot join your own rain.")
                if current.has_joined(player_id):
                    raise RainError("You already joined this rain.")
                raise RainError("This rain is no longer open.")
            self._logger.info("rain_joined", joiners=claimed.joiner_count())
        return claimed

    async def list_open(self) -> list[RainRecord]:
        return await self._rains.list_open()

    async def attach_listing(self, rain_id: str, channel_id: str, message_id: str) -> None:
        await self._rains.set_listing_ref(rain_id, channel_id, message_id)

    async def abort(self, rain_id: str) -> RainRecord:
        rain = await self.get(rain_id)
        if rain.status is not RainStatus.PENDING or rain.joiner_ids:
            raise RainError("This rain cannot be aborted.")
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        pot = SolAmount.from_lamports(rain.amount_lamports)
        now = self._clock.now()
        with LogContext.bind(rainId=rain_id):
            async with self._uow.begin() as uow:
                current = await self._rains.find_by_id(rain_id, session=uow.session)
                if current is None:
                    raise NotFoundError("That rain was not found.")
                if current.status is not RainStatus.PENDING or current.joiner_ids:
                    raise RainError("This rain cannot be aborted.")
                mutation = await self._balances.credit(
                    user_id=current.creator_id,
                    amount=pot,
                    entry_type=LedgerEntryType.REFUND,
                    transaction_type=TransactionType.REFUND,
                    price_usd=usd_price,
                    description=f"Rain abort {rain_id}",
                    game_id=rain_id,
                    session=uow.session,
                )
                current.ledger_ids.append(mutation.ledger_entry.entry_id)
                current.status = RainStatus.REFUNDED
                current.resolved_at = now
                replaced = await self._rains.replace(current, session=uow.session)
                if not replaced:
                    raise RainError("The rain could not be aborted.")
            self._logger.info("rain_aborted")
        return current

    async def settle_due(self) -> list[RainRecord]:
        now = self._clock.now()
        due = await self._rains.list_due(now)
        settled: list[RainRecord] = []
        for rain in due:
            try:
                settled.append(await self.settle(rain.rain_id))
            except Exception:
                self._logger.exception("rain_settle_failed", rainId=rain.rain_id)
        return settled

    async def settle(self, rain_id: str) -> RainRecord:
        now = self._clock.now()
        existing = await self.get(rain_id)
        if existing.status in {RainStatus.COMPLETED, RainStatus.REFUNDED}:
            return existing
        claimed = existing
        if existing.status is RainStatus.PENDING:
            if existing.expires_at is not None and existing.expires_at > now:
                raise RainError("This rain is still open.")
            locked = await self._rains.try_begin_settle(rain_id, now)
            if locked is None:
                current = await self.get(rain_id)
                if current.status in {RainStatus.COMPLETED, RainStatus.REFUNDED}:
                    return current
                if current.status is RainStatus.SETTLING:
                    claimed = current
                else:
                    raise RainError("This rain is still open.")
            else:
                claimed = locked
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        pot = SolAmount.from_lamports(claimed.amount_lamports)
        if not claimed.server_seed:
            raise RainError("This rain is missing its provably fair seed.")
        split = self._rng.resolve_rain(
            claimed.server_seed,
            claimed.client_seed,
            claimed.nonce,
            claimed.joiner_ids,
            claimed.amount_lamports,
        )
        with LogContext.bind(rainId=rain_id):
            async with self._uow.begin() as uow:
                current = await self._rains.find_by_id(rain_id, session=uow.session)
                if current is None:
                    raise NotFoundError("That rain was not found.")
                if current.status in {RainStatus.COMPLETED, RainStatus.REFUNDED}:
                    return current
                if current.joiner_ids:
                    for player_id, lamports in split.shares.items():
                        share = SolAmount.from_lamports(lamports)
                        if share.is_zero():
                            continue
                        mutation = await self._balances.credit(
                            user_id=player_id,
                            amount=share,
                            entry_type=LedgerEntryType.RAIN_PAYOUT,
                            transaction_type=TransactionType.RAIN_PAYOUT,
                            price_usd=usd_price,
                            description=f"Rain share {rain_id}",
                            game_id=rain_id,
                            session=uow.session,
                        )
                        current.ledger_ids.append(mutation.ledger_entry.entry_id)
                    current.status = RainStatus.COMPLETED
                else:
                    mutation = await self._balances.credit(
                        user_id=current.creator_id,
                        amount=pot,
                        entry_type=LedgerEntryType.REFUND,
                        transaction_type=TransactionType.REFUND,
                        price_usd=usd_price,
                        description=f"Rain refund {rain_id}",
                        game_id=rain_id,
                        session=uow.session,
                    )
                    current.ledger_ids.append(mutation.ledger_entry.entry_id)
                    current.status = RainStatus.REFUNDED
                current.payouts_lamports = split.shares
                current.fair_digest = split.digest
                current.resolved_at = now
                replaced = await self._rains.replace(current, session=uow.session)
                if not replaced:
                    raise RainError("The rain could not be settled due to a concurrent update.")
            self._logger.info(
                "rain_settled",
                status=current.status.value,
                joiners=current.joiner_count(),
            )
        return current

    def _require_duration(self, seconds: int) -> None:
        minimum = int(self._settings.min_rain_seconds)
        maximum = int(self._settings.max_rain_seconds)
        if seconds < minimum:
            raise ValidationError(f"Rain duration must be at least {minimum} seconds.")
        if seconds > maximum:
            raise ValidationError("Rain duration cannot be longer than 7 days.")
