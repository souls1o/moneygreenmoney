from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from casino.domain.enums import (
    GameStatus,
    GameType,
    LedgerEntryType,
    RainStatus,
    TransactionStatus,
    TransactionType,
)
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.rain import (
    RAIN_JOIN_STATUS,
    RainJoinEligibility,
    matches_rain_join_status,
    rain_status_texts,
)
from casino.domain.records import GameRecord, RainRecord, TransactionRecord, UserRecord
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
    MemoryGameRepository,
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
        self.games_repo = MemoryGameRepository()
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
            self.games_repo,
            self.tx_repo,
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

    def seed_wager(
        self,
        discord_id: str,
        lamports: int,
        *,
        hours_ago: float = 0,
        game_type: GameType = GameType.COINFLIP,
        limbo_target: Decimal | None = None,
    ) -> None:
        resolved = self.clock.now() - timedelta(hours=hours_ago)
        game_id = f"wager_{discord_id}_{len(self.games_repo.games)}"
        self.games_repo.games[game_id] = GameRecord(
            game_id=game_id,
            status=GameStatus.COMPLETED,
            creator_id=discord_id,
            bet_amount_lamports=lamports,
            currency="USD",
            bet_amount_fiat=Decimal("5"),
            heads_ids=[discord_id],
            player_wager_lamports={discord_id: lamports},
            game_type=game_type,
            limbo_target=limbo_target,
            created_at=resolved,
            resolved_at=resolved,
        )

    def seed_deposit(self, discord_id: str, lamports: int, *, hours_ago: float = 0) -> None:
        created = self.clock.now() - timedelta(hours=hours_ago)
        tx_id = f"dep_{discord_id}_{len(self.tx_repo.items)}"
        self.tx_repo.items[tx_id] = TransactionRecord(
            transaction_id=tx_id,
            type=TransactionType.DEPOSIT,
            status=TransactionStatus.CONFIRMED,
            amount_lamports=lamports,
            amount_usd=Decimal("5"),
            user_id=discord_id,
            created_at=created,
        )

    def qualify(self, discord_id: str, lamports: int = 50_000_000) -> None:
        self.seed_wager(discord_id, lamports)
        self.seed_deposit(discord_id, lamports)

    async def join(self, rain_id: str, player_id: str, *, status: str = RAIN_JOIN_STATUS):
        return await self.service.join_rain(rain_id, player_id, custom_statuses=(status,))

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
    async def test_admin_can_create_below_minimum(self, harness: RainHarness) -> None:
        harness.seed_user("admin")
        rain = await harness.service.create_rain(
            creator_id="admin",
            fiat_amount=Decimal("1"),
            duration_seconds=30,
            guild_id="1",
            waive_minimum=True,
        )
        assert rain.amount_lamports == SolAmount.parse("0.01").lamports
        assert rain.status is RainStatus.PENDING

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
        harness.qualify("p1", 50_000_000)
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        with pytest.raises(RainError, match="your own rain"):
            await harness.join(rain.rain_id, "creator")
        joined = await harness.join(rain.rain_id, "p1")
        assert joined.joiner_ids == ["p1"]
        with pytest.raises(RainError, match="already joined"):
            await harness.join(rain.rain_id, "p1")

    @pytest.mark.asyncio
    async def test_four_joiners_split_pot_equally(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        for player_id in ("p1", "p2", "p3", "p4"):
            harness.seed_user(player_id)
            harness.qualify(player_id, 100_000_000)
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("100"),
            duration_seconds=30,
        )
        for player_id in ("p1", "p2", "p3", "p4"):
            await harness.join(rain.rain_id, player_id)
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
        harness.qualify("p1", 50_000_000)
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        with pytest.raises(RainError, match="still open"):
            await harness.service.settle(rain.rain_id)
        harness.clock.advance(30)
        with pytest.raises(RainError, match="already ended"):
            await harness.join(rain.rain_id, "p1")

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
        harness.qualify("p1", 50_000_000)
        harness.qualify("p2", 50_000_000)
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=10,
        )
        await harness.join(rain.rain_id, "p1")
        await harness.join(rain.rain_id, "p2")
        harness.clock.advance(10)
        settled = await harness.service.settle_due()
        assert len(settled) == 1
        assert settled[0].status is RainStatus.COMPLETED
        assert sum(settled[0].payouts_lamports.values()) == settled[0].amount_lamports
        assert harness.balance("p1") == harness.balance("p2")

    @pytest.mark.asyncio
    async def test_join_requires_ten_percent_wagered_since_rain_started(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        harness.seed_user("p1")
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        with pytest.raises(RainError, match="wagered at least"):
            await harness.join(rain.rain_id, "p1")
        harness.seed_wager("p1", 49_999_999)
        with pytest.raises(RainError, match=r"\$5\.00"):
            await harness.join(rain.rain_id, "p1")
        harness.seed_wager("p1", 1)
        harness.seed_deposit("p1", 50_000_000)
        joined = await harness.join(rain.rain_id, "p1")
        assert joined.has_joined("p1")

    @pytest.mark.asyncio
    async def test_join_rejects_wager_from_before_rain_started(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        harness.seed_user("p1")
        harness.seed_wager("p1", 50_000_000)
        harness.seed_deposit("p1", 50_000_000)
        harness.clock.advance(1)
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        with pytest.raises(RainError, match="wagered at least"):
            await harness.join(rain.rain_id, "p1")

    @pytest.mark.asyncio
    async def test_join_requires_ten_percent_deposited_since_rain_started(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        harness.seed_user("p1")
        harness.seed_wager("p1", 50_000_000)
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        with pytest.raises(RainError, match="deposited at least"):
            await harness.join(rain.rain_id, "p1")
        harness.seed_deposit("p1", 49_999_999)
        with pytest.raises(RainError, match=r"\$5\.00"):
            await harness.join(rain.rain_id, "p1")
        harness.seed_deposit("p1", 1)
        joined = await harness.join(rain.rain_id, "p1")
        assert joined.has_joined("p1")

    @pytest.mark.asyncio
    async def test_join_rejects_deposit_from_before_rain_started(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        harness.seed_user("p1")
        harness.seed_deposit("p1", 50_000_000)
        harness.clock.advance(1)
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        harness.seed_wager("p1", 50_000_000)
        with pytest.raises(RainError, match="deposited at least"):
            await harness.join(rain.rain_id, "p1")

    @pytest.mark.asyncio
    async def test_join_requires_custom_status(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        harness.seed_user("p1")
        harness.qualify("p1", 50_000_000)
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        with pytest.raises(RainError, match="custom status"):
            await harness.service.join_rain(rain.rain_id, "p1")
        with pytest.raises(RainError, match="custom status"):
            await harness.join(rain.rain_id, "p1", status="playing slots")
        joined = await harness.join(rain.rain_id, "p1", status=f"  {RAIN_JOIN_STATUS}  ")
        assert joined.has_joined("p1")

    @pytest.mark.asyncio
    async def test_evaluate_join_reports_each_requirement(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        harness.seed_user("p1")
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        harness.seed_wager("p1", 50_000_000)
        check = await harness.service.evaluate_join(rain.rain_id, "p1")
        assert check.wager_ok is True
        assert check.deposit_ok is False
        assert check.status_ok is False
        assert check.required_fiat == Decimal("5.00")
        assert check.wagered_fiat == Decimal("5.00")
        assert check.deposited_fiat == Decimal("0.00")
        assert check.requirements_met() is False
        harness.seed_deposit("p1", 50_000_000)
        ready = await harness.service.evaluate_join(
            rain.rain_id, "p1", custom_statuses=(RAIN_JOIN_STATUS,)
        )
        assert ready.requirements_met() is True

    @pytest.mark.asyncio
    async def test_admin_rain_uses_custom_usd_requirements(self, harness: RainHarness) -> None:
        harness.seed_user("admin")
        harness.seed_user("p1")
        rain = await harness.service.create_rain(
            creator_id="admin",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
            guild_id="1",
            waive_minimum=True,
            wager_requirement_usd=Decimal("10"),
            deposit_requirement_usd=Decimal("1"),
        )
        assert rain.wager_required_fiat == Decimal("10.00")
        assert rain.deposit_required_fiat == Decimal("1.00")
        harness.seed_wager("p1", 50_000_000)
        harness.seed_deposit("p1", 10_000_000)
        with pytest.raises(RainError, match="wagered at least"):
            await harness.join(rain.rain_id, "p1")
        harness.seed_wager("p1", 50_000_000)
        joined = await harness.join(rain.rain_id, "p1")
        assert joined.has_joined("p1")

    @pytest.mark.asyncio
    async def test_limbo_low_multiplier_does_not_fully_count(self, harness: RainHarness) -> None:
        harness.seed_user("creator")
        harness.seed_user("p1")
        rain = await harness.service.create_rain(
            creator_id="creator",
            fiat_amount=Decimal("50"),
            duration_seconds=30,
        )
        harness.seed_wager(
            "p1",
            50_000_000,
            game_type=GameType.LIMBO,
            limbo_target=Decimal("1.01"),
        )
        harness.seed_deposit("p1", 50_000_000)
        with pytest.raises(RainError, match="wagered at least"):
            await harness.join(rain.rain_id, "p1")
        harness.seed_wager("p1", 50_000_000)
        joined = await harness.join(rain.rain_id, "p1")
        assert joined.has_joined("p1")


def test_rain_join_status_matches_custom_activity() -> None:
    assert matches_rain_join_status(RAIN_JOIN_STATUS)
    assert matches_rain_join_status(f"  {RAIN_JOIN_STATUS}  ")
    assert matches_rain_join_status(RAIN_JOIN_STATUS.replace("#", "\u2060#"))
    assert not matches_rain_join_status("playing")
    member = type(
        "Member",
        (),
        {
            "activities": (
                type("Activity", (), {"name": "Custom Status", "state": RAIN_JOIN_STATUS})(),
            ),
            "activity": None,
        },
    )()
    assert rain_status_texts(member) == (RAIN_JOIN_STATUS,)


class _EmbedContainer:
    math = MathService(300)
    settings = StubSettings()


def test_rain_listing_omits_join_requirements() -> None:
    factory = EmbedFactory(_EmbedContainer())  # type: ignore[arg-type]
    rain = RainRecord(
        rain_id="r1",
        status=RainStatus.PENDING,
        creator_id="1",
        amount_lamports=500_000_000,
        currency="USD",
        amount_fiat=Decimal("50"),
    )
    text = _panel_text(factory.rain_listing(rain))
    assert "Click Join" in text
    assert "10%" not in text
    assert RAIN_JOIN_STATUS not in text


def test_rain_join_gate_marks_completed_and_missing() -> None:
    factory = EmbedFactory(_EmbedContainer())  # type: ignore[arg-type]
    missing = RainJoinEligibility(
        currency="USD",
        wager_required_fiat=Decimal("5"),
        deposit_required_fiat=Decimal("5"),
        wagered_fiat=Decimal("5"),
        deposited_fiat=Decimal("1"),
        wager_ok=True,
        deposit_ok=False,
        status_ok=False,
        is_creator=False,
        already_joined=False,
        is_open=True,
        has_ended=False,
    )
    text = _panel_text(factory.rain_join_gate(missing))
    assert "Rain requirements" in text
    assert "✅ $5.00 / $5.00" in text
    assert "❌ $1.00 / $5.00" in text
    assert "❌" in text
    assert "casinounderground" in text
    ready = RainJoinEligibility(
        currency="USD",
        wager_required_fiat=Decimal("5"),
        deposit_required_fiat=Decimal("5"),
        wagered_fiat=Decimal("5"),
        deposited_fiat=Decimal("5"),
        wager_ok=True,
        deposit_ok=True,
        status_ok=True,
        is_creator=False,
        already_joined=False,
        is_open=True,
        has_ended=False,
    )
    joined = _panel_text(factory.rain_join_gate(ready, joined=True))
    assert "Joined the rain" in joined
    assert "**Deposit**" in joined
    assert "✅ $5.00 / $5.00" in joined


def _walk_items(view) -> list[object]:
    items: list[object] = []
    seen: set[int] = set()
    stack = [view]
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        items.append(current)
        children = getattr(current, "children", None) or getattr(current, "_children", None) or ()
        stack.extend(list(children))
        walked = getattr(current, "walk_children", None)
        if callable(walked):
            stack.extend(list(walked()))
    return items


def _panel_text(panel) -> str:
    chunks: list[str] = []
    for item in _walk_items(panel.view):
        content = getattr(item, "content", None)
        if content:
            chunks.append(str(content))
    return "\n".join(chunks)
