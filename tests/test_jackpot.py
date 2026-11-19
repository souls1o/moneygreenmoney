from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.enums import JackpotStatus
from casino.domain.jackpot import pick_weighted_winner, require_join_amount, win_chance
from casino.domain.records import UserRecord
from casino.errors import JackpotError
from casino.services.balance_service import BalanceService
from casino.services.jackpot_service import JackpotService
from casino.services.ledger_service import LedgerService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService, SolAmount
from casino.utils.rng import ProvablyFairService
from tests.conftest import FrozenClock, StubPriceService, StubSettings
from tests.fakes import (
    MemoryHouseRepository,
    MemoryJackpotRepository,
    MemoryLedgerRepository,
    MemoryTransactionRepository,
    MemoryUserRepository,
    NullUnitOfWorkFactory,
)


class JackpotHarness:
    def __init__(self) -> None:
        self.clock = FrozenClock()
        self.ids = IdentifierFactory()
        self.math = MathService(300)
        self.users_repo = MemoryUserRepository()
        self.jackpots_repo = MemoryJackpotRepository()
        self.ledger_repo = MemoryLedgerRepository()
        self.tx_repo = MemoryTransactionRepository()
        self.house_repo = MemoryHouseRepository()
        ledger = LedgerService(self.ledger_repo, self.ids, self.clock)
        balances = BalanceService(
            self.users_repo, self.tx_repo, self.house_repo, ledger, self.ids, self.clock
        )
        settings = StubSettings()
        users = UserService(self.users_repo, self.ids, self.clock, settings)  # type: ignore[arg-type]
        self.service = JackpotService(
            self.jackpots_repo,
            users,
            self.users_repo,
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

    def seed_user(self, discord_id: str, sol: str = "10") -> UserRecord:
        user = UserRecord(
            discord_id=discord_id,
            sol_balance_lamports=SolAmount.parse(sol).lamports,
            deposit_memo=self.ids.deposit_memo(),
            display_currency="USD",
            created_at=self.clock.now(),
            updated_at=self.clock.now(),
        )
        self.users_repo.users[discord_id] = user
        return user

    def balance(self, discord_id: str) -> int:
        return self.users_repo.users[discord_id].sol_balance_lamports


@pytest.fixture
def harness() -> JackpotHarness:
    return JackpotHarness()


class TestJackpotDomain:
    def test_later_join_cannot_exceed_ten_times_opener(self) -> None:
        require_join_amount(1_000, 10_000)
        with pytest.raises(JackpotError, match="10×"):
            require_join_amount(1_000, 10_001)

    def test_weighted_ticket_uses_bet_share_of_pot(self) -> None:
        entries = {"a": 1, "b": 3}
        assert pick_weighted_winner(entries, 0) == "a"
        assert pick_weighted_winner(entries, 1) == "b"
        assert pick_weighted_winner(entries, 3) == "b"
        assert win_chance(1, 4) == 0.25


class TestJackpotService:
    @pytest.mark.asyncio
    async def test_opener_starts_timer_and_later_join_is_capped(self, harness: JackpotHarness) -> None:
        harness.seed_user("alice")
        harness.seed_user("bob")
        live = await harness.service.ensure_open("guild")
        opened = await harness.service.join(live.jackpot_id, "alice", Decimal("1"))
        assert opened.opener_id == "alice"
        assert opened.expires_at is not None
        with pytest.raises(JackpotError, match="10×"):
            await harness.service.join(live.jackpot_id, "bob", Decimal("10.01"))
        joined = await harness.service.join(live.jackpot_id, "bob", Decimal("10"))
        assert joined.entries_lamports["bob"] == SolAmount.parse("0.1").lamports
        assert joined.pot_lamports() == SolAmount.parse("0.11").lamports

    @pytest.mark.asyncio
    async def test_solo_timeout_refunds_and_shows_result(self, harness: JackpotHarness) -> None:
        harness.seed_user("alice")
        live = await harness.service.ensure_open("guild")
        await harness.service.join(live.jackpot_id, "alice", Decimal("2"))
        before = harness.balance("alice")
        harness.clock.advance(60)
        settled = await harness.service.settle(live.jackpot_id)
        assert settled.status is JackpotStatus.SHOWING
        assert settled.winner_id is None
        assert harness.balance("alice") == before + SolAmount.parse("0.02").lamports
        harness.clock.advance(10)
        nxt = await harness.service.reset_after_reveal(live.jackpot_id)
        assert nxt.jackpot_id != live.jackpot_id
        assert nxt.status is JackpotStatus.OPEN
        assert not nxt.entries_lamports
        stored = harness.jackpots_repo.items[live.jackpot_id]
        assert stored.status is JackpotStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_winner_takes_pot_minus_fee(self, harness: JackpotHarness) -> None:
        harness.seed_user("alice")
        harness.seed_user("bob")
        live = await harness.service.ensure_open("guild")
        await harness.service.join(live.jackpot_id, "alice", Decimal("1"))
        await harness.service.join(live.jackpot_id, "bob", Decimal("3"))
        harness.clock.advance(60)
        settled = await harness.service.settle(live.jackpot_id)
        assert settled.status is JackpotStatus.SHOWING
        assert settled.winner_id in {"alice", "bob"}
        pot = SolAmount.parse("0.04")
        split = harness.math.split_pot(pot)
        assert settled.winner_payout_lamports == split.winner_payout.lamports
        assert settled.house_fee_lamports == split.house_fee.lamports
        start = SolAmount.parse("10").lamports
        assert harness.balance(settled.winner_id) == (
            start - settled.entries_lamports[settled.winner_id] + settled.winner_payout_lamports
        )
        loser = "bob" if settled.winner_id == "alice" else "alice"
        assert harness.balance(loser) == start - settled.entries_lamports[loser]
