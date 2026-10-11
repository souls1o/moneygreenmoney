from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.enums import LedgerEntryType, RainStatus
from casino.domain.records import UserRecord
from casino.errors import RainError, ValidationError
from casino.services.balance_service import BalanceService
from casino.services.ledger_service import LedgerService
from casino.services.rain_service import RainService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService, SolAmount
from casino.utils.rng import ProvablyFairService
from tests.conftest import FrozenClock, StubPriceService, StubSettings
from tests.fakes import (
    MemoryHouseRepository,
    MemoryLedgerRepository,
    MemoryRainRepository,
    MemoryTransactionRepository,
    MemoryUserRepository,
    NullUnitOfWorkFactory,
)


class RainHarness:
    def __init__(self) -> None:
        self.clock = FrozenClock()
        self.ids = IdentifierFactory()
        self.math = MathService(400)
        self.users_repo = MemoryUserRepository()
        self.rains_repo = MemoryRainRepository()
        self.ledger_repo = MemoryLedgerRepository()
        self.tx_repo = MemoryTransactionRepository()
        self.house_repo = MemoryHouseRepository()
        ledger = LedgerService(self.ledger_repo, self.ids, self.clock)
        balances = BalanceService(
            self.users_repo, self.tx_repo, self.house_repo, ledger, self.ids, self.clock
        )
        settings = StubSettings()
        users = UserService(self.users_repo, self.ids, self.clock, settings)  # type: ignore[arg-type]
        self.service = RainService(
            self.rains_repo,
            users,
            balances,
            StubPriceService(Decimal("100")),
            ValidationService(self.math, Decimal("0.01"), ("USD", "EUR")),
            self.math,
            ProvablyFairService(),
            self.ids,
            self.clock,
            NullUnitOfWorkFactory(),
            settings,  # type: ignore[arg-type]
            self.house_repo,
        )

    def seed_user(self, discord_id: str, sol: str = "10", currency: str = "USD") -> UserRecord:
        user = UserRecord(
            discord_id=discord_id,
            sol_balance_lamports=SolAmount.parse(sol).lamports,
            deposit_memo=self.ids.deposit_memo(),
            display_currency=currency,
            created_at=self.clock.now(),
            updated_at=self.clock.now(),
        )
        self.users_repo.users[discord_id] = user
        return user

    def balance(self, discord_id: str) -> int:
        return self.users_repo.users[discord_id].sol_balance_lamports


@pytest.fixture
def harness() -> RainHarness:
    return RainHarness()


class TestValidationDuration:
    def test_parses_units_and_bare_seconds(self) -> None:
        validation = ValidationService(MathService(400), Decimal("0.01"), ("USD",))
        assert validation.parse_duration_seconds("30s") == 30
        assert validation.parse_duration_seconds("5m") == 300
        assert validation.parse_duration_seconds("2h") == 7200
        assert validation.parse_duration_seconds("1d") == 86_400
        assert validation.parse_duration_seconds("45") == 45

    def test_rejects_empty_and_non_positive(self) -> None:
        validation = ValidationService(MathService(400), Decimal("0.01"), ("USD",))
        with pytest.raises(ValidationError):
            validation.parse_duration_seconds("")
        with pytest.raises(ValidationError):
            validation.parse_duration_seconds("0s")
        with pytest.raises(ValidationError):
            validation.parse_duration_seconds("nope")


class TestRainService:
    @pytest.mark.asyncio
    async def test_create_debits_creator_and_rejects_minimum(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        with pytest.raises(ValidationError, match="Minimum rain"):
            await harness.service.create_rain(
                creator_id="creator",
                fiat_amount=Decimal("49"),
                duration_seconds=30,
                guild_id="1",
            )
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
            guild_id="1",
        )
        assert rain.status is RainStatus.PENDING
        assert rain.amount_lamports == SolAmount.parse("0.5").lamports
        assert rain.creator_id == "creator"
        assert rain.server_seed_hash
        assert rain.server_seed
        assert harness.balance("creator") == SolAmount.parse("9.5").lamports
        assert any(entry.type is LedgerEntryType.RAIN for entry in harness.ledger_repo.entries)

    @pytest.mark.asyncio
    async def test_duration_bounds(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        with pytest.raises(ValidationError, match="at least 10"):
            await harness.service.create_rain(
                creator_id="creator",
                fiat_amount=Decimal("50"),
                duration_seconds=9,
            )
        with pytest.raises(ValidationError, match="7 days"):
            await harness.service.create_rain(
                creator_id="creator",
                fiat_amount=Decimal("50"),
                duration_seconds=604_801,
            )

    @pytest.mark.asyncio
    async def test_creator_cannot_join_and_joiners_cannot_double_join(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        harness.seed_user("p1")
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        with pytest.raises(RainError, match="your own rain"):
            await harness.service.join_rain(rain.rain_id, "creator")
        joined = await harness.service.join_rain(rain.rain_id, "p1")
        assert joined.joiner_ids == ["p1"]
        with pytest.raises(RainError, match="already joined"):
            await harness.service.join_rain(rain.rain_id, "p1")

    @pytest.mark.asyncio
    async def test_four_joiners_split_pot_equally(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        for player_id in ("p1", "p2", "p3", "p4"):
            harness.seed_user(player_id)
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("100"),
            duration_seconds=30,
        )
        for player_id in ("p1", "p2", "p3", "p4"):
            await harness.service.join_rain(rain.rain_id, player_id)
        harness.clock.advance(30)
        settled = await harness.service.settle(rain.rain_id)
        assert settled.status is RainStatus.COMPLETED
        share = SolAmount.parse("0.25").lamports
        assert settled.payouts_lamports == {player_id: share for player_id in ("p1", "p2", "p3", "p4")}
        for player_id in ("p1", "p2", "p3", "p4"):
            assert harness.balance(player_id) == SolAmount.parse("10.25").lamports
        assert sum(settled.payouts_lamports.values()) == settled.amount_lamports
        replay = ProvablyFairService().resolve_rain(
            settled.server_seed or "",
            settled.client_seed,
            settled.nonce,
            settled.joiner_ids,
            settled.amount_lamports,
        )
        assert replay.digest == settled.fair_digest
        assert replay.shares == settled.payouts_lamports

    @pytest.mark.asyncio
    async def test_no_joiners_refunds_creator(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=10,
        )
        assert harness.balance("creator") == SolAmount.parse("9.5").lamports
        harness.clock.advance(10)
        settled = await harness.service.settle(rain.rain_id)
        assert settled.status is RainStatus.REFUNDED
        assert settled.payouts_lamports == {}
        assert harness.balance("creator") == SolAmount.parse("10").lamports

    @pytest.mark.asyncio
    async def test_cannot_join_or_settle_while_open(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        harness.seed_user("p1")
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        with pytest.raises(RainError, match="still open"):
            await harness.service.settle(rain.rain_id)
        harness.clock.advance(30)
        with pytest.raises(RainError, match="already ended"):
            await harness.service.join_rain(rain.rain_id, "p1")

    @pytest.mark.asyncio
    async def test_abort_refunds_unposted_rain(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        aborted = await harness.service.abort(rain.rain_id)
        assert aborted.status is RainStatus.REFUNDED
        assert harness.balance("creator") == SolAmount.parse("10").lamports

    @pytest.mark.asyncio
    async def test_settle_due_pays_joiners(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        harness.seed_user("p1")
        harness.seed_user("p2")
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=10,
        )
        await harness.service.join_rain(rain.rain_id, "p1")
        await harness.service.join_rain(rain.rain_id, "p2")
        harness.clock.advance(10)
        settled = await harness.service.settle_due()
        assert len(settled) == 1
        assert settled[0].status is RainStatus.COMPLETED
        assert sum(settled[0].payouts_lamports.values()) == settled[0].amount_lamports
        assert harness.balance("p1") == harness.balance("p2")
