from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from casino.config.settings import Settings
from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.enums import LedgerEntryType, RainStatus, TransactionType
from casino.domain.rain import (
    RAIN_JOIN_STATUS,
    RainJoinEligibility,
    default_rain_requirement_fiat,
    default_rain_requirement_lamports,
    matches_rain_join_status,
    parse_rain_requirement_usd,
)
from casino.domain.records import RainRecord
from casino.errors import NotFoundError, RainError, ValidationError
from casino.logging.logger import LogContext, get_logger
from casino.repositories.game_repository import GameRepository
from casino.repositories.rain_repository import RainRepository
from casino.repositories.transaction_repository import TransactionRepository
from casino.services.balance_service import BalanceService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.clock import Clock
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import FIAT_QUANTUM, MathService, SolAmount
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
        game_repository: GameRepository,
        transaction_repository: TransactionRepository,
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
        self._games = game_repository
        self._transactions = transaction_repository
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
        waive_minimum: bool = False,
        wager_requirement_usd: Decimal | None = None,
        deposit_requirement_usd: Decimal | None = None,
    ) -> RainRecord:
        self._require_duration(duration_seconds)
        user = await self._users.get_or_create(creator_id)
        quote = await self._prices.get_quote()
        currency = user.display_currency
        sol_price = quote.sol_price(currency)
        usd_price = quote.sol_price("USD")
        amount_usd = fiat_amount * usd_price / sol_price
        minimum = Decimal(self._settings.min_rain_usd)
        if not waive_minimum and amount_usd < minimum:
            raise ValidationError(
                f"Minimum rain is {self._math.format_fiat_symbol(minimum, 'USD')}."
            )
        pot = self._math.fiat_to_sol(fiat_amount, sol_price)
        now = self._clock.now()
        commitment = self._rng.commit()
        wager_required_lamports, wager_required_fiat = self._requirement(
            pot.lamports,
            fiat_amount,
            currency,
            usd_price,
            sol_price,
            wager_requirement_usd,
            deposit=False,
        )
        deposit_required_lamports, deposit_required_fiat = self._requirement(
            pot.lamports,
            fiat_amount,
            currency,
            usd_price,
            sol_price,
            deposit_requirement_usd,
            deposit=True,
        )
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
            wager_required_lamports=wager_required_lamports,
            deposit_required_lamports=deposit_required_lamports,
            wager_required_fiat=wager_required_fiat,
            deposit_required_fiat=deposit_required_fiat,
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

    async def join_rain(
        self,
        rain_id: str,
        player_id: str,
        *,
        custom_statuses: Sequence[str] = (),
    ) -> RainRecord:
        eligibility = await self.evaluate_join(rain_id, player_id, custom_statuses=custom_statuses)
        if eligibility.is_creator:
            raise RainError("You cannot join your own rain.")
        if eligibility.already_joined:
            raise RainError("You already joined this rain.")
        if not eligibility.is_open:
            raise RainError("This rain is no longer open.")
        if eligibility.has_ended:
            raise RainError("This rain has already ended.")
        if not eligibility.wager_ok:
            raise RainError(
                "You must have wagered at least "
                f"{self._math.format_fiat_symbol(eligibility.wager_required_fiat, eligibility.currency)} "
                "since this rain started to join."
            )
        if not eligibility.deposit_ok:
            raise RainError(
                "You must have deposited at least "
                f"{self._math.format_fiat_symbol(eligibility.deposit_required_fiat, eligibility.currency)} "
                "since this rain started to join."
            )
        if not eligibility.status_ok:
            raise RainError(
                "Set your Discord custom status to "
                f"`{RAIN_JOIN_STATUS}` to join this rain."
            )
        now = self._clock.now()
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

    async def evaluate_join(
        self,
        rain_id: str,
        player_id: str,
        *,
        custom_statuses: Sequence[str] = (),
    ) -> RainJoinEligibility:
        await self._users.get_or_create(player_id)
        rain = await self.get(rain_id)
        now = self._clock.now()
        wager_required = rain.wager_requirement_lamports()
        deposit_required = rain.deposit_requirement_lamports()
        since = rain.created_at or now
        try:
            wagered = await self._games.sum_player_wagered_since(player_id, since)
            deposited = await self._transactions.sum_player_deposited_since(player_id, since)
        except Exception:
            self._logger.exception("rain_join_progress_failed", userId=player_id)
            raise RainError("Could not check rain requirements. Try again in a moment.")
        return RainJoinEligibility(
            currency=rain.currency,
            wager_required_fiat=rain.wager_requirement_fiat(),
            deposit_required_fiat=rain.deposit_requirement_fiat(),
            wagered_fiat=self._progress_fiat(rain, wagered),
            deposited_fiat=self._progress_fiat(rain, deposited),
            wager_ok=wagered >= wager_required,
            deposit_ok=deposited >= deposit_required,
            status_ok=matches_rain_join_status(*custom_statuses),
            is_creator=rain.creator_id == player_id,
            already_joined=rain.has_joined(player_id),
            is_open=rain.is_open(),
            has_ended=rain.expires_at is not None and rain.expires_at <= now,
        )

    def _progress_fiat(self, rain: RainRecord, lamports: int) -> Decimal:
        if rain.amount_lamports <= 0:
            return Decimal("0")
        return (
            rain.amount_fiat * Decimal(max(int(lamports), 0)) / Decimal(rain.amount_lamports)
        ).quantize(FIAT_QUANTUM, rounding=ROUND_DOWN)

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

    def _requirement(
        self,
        amount_lamports: int,
        amount_fiat: Decimal,
        currency: str,
        usd_price: Decimal,
        sol_price: Decimal,
        requirement_usd: Decimal | None,
        *,
        deposit: bool,
    ) -> tuple[int, Decimal]:
        if requirement_usd is None:
            return (
                default_rain_requirement_lamports(amount_lamports, deposit=deposit),
                default_rain_requirement_fiat(amount_fiat, deposit=deposit),
            )
        usd = parse_rain_requirement_usd(requirement_usd)
        lamports = self._math.fiat_to_sol(usd, usd_price).lamports
        fiat = usd if currency.upper() == "USD" else (usd * sol_price / usd_price).quantize(
            FIAT_QUANTUM, rounding=ROUND_DOWN
        )
        return lamports, fiat

    def _require_duration(self, seconds: int) -> None:
        minimum = int(self._settings.min_rain_seconds)
        maximum = int(self._settings.max_rain_seconds)
        if seconds < minimum:
            raise ValidationError(f"Rain duration must be at least {minimum} seconds.")
        if seconds > maximum:
            raise ValidationError("Rain duration cannot be longer than 7 days.")
