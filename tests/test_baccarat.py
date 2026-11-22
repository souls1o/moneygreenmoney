from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.baccarat import (
    BaccaratSide,
    banker_draws_third,
    baccarat_total,
    deal_baccarat_from_cards,
    payout_lamports,
    profit_lamports,
)
from casino.domain.blackjack import BlackjackCard
from casino.domain.enums import BaccaratStatus
from casino.domain.records import UserRecord
from casino.errors import BaccaratError
from casino.services.baccarat_service import BaccaratService
from casino.services.balance_service import BalanceService
from casino.services.ledger_service import LedgerService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService, SolAmount
from casino.utils.rng import FairCard, ProvablyFairService
from tests.conftest import FrozenClock, StubPriceService, StubSettings
from tests.fakes import (
    MemoryBaccaratRepository,
    MemoryHouseRepository,
    MemoryLedgerRepository,
    MemoryTransactionRepository,
    MemoryUserRepository,
    NullUnitOfWorkFactory,
)

NINE = BlackjackCard(rank=8, suit=0)
SEVEN = BlackjackCard(rank=6, suit=0)
KING = BlackjackCard(rank=12, suit=0)
ACE = BlackjackCard(rank=0, suit=0)
FOUR = BlackjackCard(rank=3, suit=0)
TWO = BlackjackCard(rank=1, suit=0)
FIVE = BlackjackCard(rank=4, suit=0)
SIX = BlackjackCard(rank=5, suit=0)
EIGHT = BlackjackCard(rank=7, suit=0)


class ScriptedCards(ProvablyFairService):
    def __init__(self, cards: list[BlackjackCard]) -> None:
        super().__init__()
        self._cards = cards

    def resolve_card(self, server_seed: str, client_seed: str, nonce: int, card_index: int) -> FairCard:
        return FairCard(digest="d", card=self._cards[card_index], card_index=card_index)


class BaccaratHarness:
    def __init__(self, cards: list[BlackjackCard] | None = None) -> None:
        self.clock = FrozenClock()
        self.ids = IdentifierFactory()
        self.math = MathService(300)
        self.users_repo = MemoryUserRepository()
        self.tables_repo = MemoryBaccaratRepository()
        self.ledger_repo = MemoryLedgerRepository()
        self.tx_repo = MemoryTransactionRepository()
        self.house_repo = MemoryHouseRepository()
        self.house_repo.record.sol_balance_lamports = SolAmount.parse("50").lamports
        ledger = LedgerService(self.ledger_repo, self.ids, self.clock)
        balances = BalanceService(
            self.users_repo, self.tx_repo, self.house_repo, ledger, self.ids, self.clock
        )
        settings = StubSettings()
        users = UserService(self.users_repo, self.ids, self.clock, settings)  # type: ignore[arg-type]
        self.service = BaccaratService(
            self.tables_repo,
            users,
            self.users_repo,
            balances,
            StubPriceService(Decimal("100")),
            ValidationService(self.math, Decimal("0.01"), ("USD", "EUR")),
            self.math,
            ScriptedCards(cards or [NINE, SEVEN, KING, KING]),
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


def test_naturals_skip_third_cards() -> None:
    deal = deal_baccarat_from_cards([NINE, SEVEN, KING, KING])
    assert deal.player_total == 9
    assert deal.banker_total == 7
    assert deal.outcome is BaccaratSide.PLAYER
    assert len(deal.player_cards) == 2
    assert len(deal.banker_cards) == 2


def test_player_draws_and_banker_tableau() -> None:
    deal = deal_baccarat_from_cards([TWO, SIX, TWO, ACE, FIVE])
    assert baccarat_total(deal.player_cards) == 9
    assert deal.banker_total == 7
    assert len(deal.player_cards) == 3
    assert len(deal.banker_cards) == 2
    assert deal.outcome is BaccaratSide.PLAYER


def test_banker_draws_when_player_stands() -> None:
    deal = deal_baccarat_from_cards([SIX, TWO, ACE, TWO, FIVE])
    assert deal.player_total == 7
    assert len(deal.player_cards) == 2
    assert len(deal.banker_cards) == 3
    assert deal.banker_total == 9


def test_banker_does_not_draw_on_three_vs_player_eight() -> None:
    assert banker_draws_third(3, 8) is False
    assert banker_draws_third(3, 0) is True


def test_banker_commission_and_tie_payouts() -> None:
    assert profit_lamports(100, BaccaratSide.PLAYER) == 100
    assert profit_lamports(100, BaccaratSide.BANKER) == 95
    assert profit_lamports(100, BaccaratSide.TIE) == 800
    result, payout = payout_lamports(100, BaccaratSide.PLAYER, BaccaratSide.TIE)
    assert result.value == "push"
    assert payout == 100
    result, payout = payout_lamports(100, BaccaratSide.BANKER, BaccaratSide.BANKER)
    assert payout == 195


@pytest.mark.asyncio
async def test_first_bet_starts_timer_and_player_natural_pays() -> None:
    harness = BaccaratHarness([NINE, SEVEN, KING, KING])
    harness.seed_user("alice")
    live = await harness.service.ensure_open("guild")
    placed = await harness.service.place_bet(live.baccarat_id, "alice", BaccaratSide.PLAYER, Decimal("1"))
    assert placed.opener_id == "alice"
    assert placed.expires_at is not None
    with pytest.raises(BaccaratError, match="already"):
        await harness.service.place_bet(live.baccarat_id, "alice", BaccaratSide.BANKER, Decimal("1"))
    harness.clock.advance(30)
    settled = await harness.service.settle(live.baccarat_id)
    assert settled.status is BaccaratStatus.SHOWING
    assert settled.outcome is BaccaratSide.PLAYER
    assert settled.payouts_lamports["alice"] == SolAmount.parse("0.02").lamports
    assert harness.balance("alice") == SolAmount.parse("10.01").lamports


@pytest.mark.asyncio
async def test_tie_pushes_player_and_banker_and_pays_tie() -> None:
    harness = BaccaratHarness([EIGHT, EIGHT, KING, KING])
    harness.seed_user("alice")
    harness.seed_user("bob")
    harness.seed_user("cara")
    live = await harness.service.ensure_open("guild")
    await harness.service.place_bet(live.baccarat_id, "alice", BaccaratSide.PLAYER, Decimal("1"))
    await harness.service.place_bet(live.baccarat_id, "bob", BaccaratSide.BANKER, Decimal("1"))
    await harness.service.place_bet(live.baccarat_id, "cara", BaccaratSide.TIE, Decimal("1"))
    harness.clock.advance(30)
    settled = await harness.service.settle(live.baccarat_id)
    assert settled.outcome is BaccaratSide.TIE
    assert settled.payouts_lamports["alice"] == SolAmount.parse("0.01").lamports
    assert settled.payouts_lamports["bob"] == SolAmount.parse("0.01").lamports
    assert settled.payouts_lamports["cara"] == SolAmount.parse("0.09").lamports
    assert harness.balance("alice") == SolAmount.parse("10").lamports
    assert harness.balance("bob") == SolAmount.parse("10").lamports
    assert harness.balance("cara") == SolAmount.parse("10.08").lamports
