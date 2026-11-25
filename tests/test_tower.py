from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.enums import CoinSide, GameStatus, GameType
from casino.domain.records import HOUSE_USER_ID
from casino.domain.tower import parse_tower_difficulty, tower_multiplier, tower_payout_lamports
from casino.errors import GameError
from tests.conftest import FixedRandomService
from tests.test_game_service import GameHarness


def test_parse_tower_difficulty() -> None:
    assert parse_tower_difficulty("easy") == "easy"
    with pytest.raises(GameError):
        parse_tower_difficulty("insane")


def test_tower_multiplier_applies_three_percent_edge() -> None:
    assert tower_multiplier(4, 0) == Decimal("1.00")
    assert tower_multiplier(4, 1) == Decimal("1.29")
    assert tower_payout_lamports(1_000_000_000, 4, 1) == 1_293_333_333


@pytest.mark.asyncio
async def test_tower_safe_pick_then_cashout_pays_multiplier() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(CoinSide.HEADS, tower=[1] * 8)
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.TOWER,
        tower_difficulty="easy",
    )
    assert game.status is GameStatus.ACTIVE
    climbed = await harness.service.tower_pick(game.game_id, "1", 0)
    assert climbed.status is GameStatus.ACTIVE
    cashed = await harness.service.tower_cashout(game.game_id, "1")
    assert cashed.status is GameStatus.COMPLETED
    assert cashed.winner_ids == ["1"]
    payout = tower_payout_lamports(50_000_000, 4, 1)
    profit = payout - 50_000_000
    assert cashed.winner_payout_lamports == payout
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000 + payout
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000 - profit


@pytest.mark.asyncio
async def test_tower_bomb_credits_house() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(CoinSide.HEADS, tower=[0] * 8)
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    start_house = harness.house_repo.record.sol_balance_lamports
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.TOWER,
        tower_difficulty="easy",
    )
    lost = await harness.service.tower_pick(game.game_id, "1", 0)
    assert lost.status is GameStatus.COMPLETED
    assert lost.winner_ids == [HOUSE_USER_ID]
    assert lost.tower_hit == 0
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000
    assert harness.house_repo.record.sol_balance_lamports == start_house + 50_000_000
