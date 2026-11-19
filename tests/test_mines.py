from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.enums import CoinSide, GameStatus, GameType
from casino.domain.mines import (
    mines_multiplier,
    mines_payout_lamports,
    parse_mines_count,
)
from casino.domain.records import HOUSE_USER_ID
from casino.errors import GameError
from tests.conftest import FixedRandomService
from tests.test_game_service import GameHarness


def test_parse_mines_count() -> None:
    assert parse_mines_count("3") == 3
    with pytest.raises(GameError):
        parse_mines_count("0")
    with pytest.raises(GameError):
        parse_mines_count("25")


def test_mines_multiplier_applies_three_percent_edge() -> None:
    assert mines_multiplier(1, 0) == Decimal("1.00")
    assert mines_multiplier(1, 1) == Decimal("1.01")
    assert mines_payout_lamports(1_000_000_000, 1, 1) == 1_010_416_666


@pytest.mark.asyncio
async def test_mines_gem_then_cashout_pays_multiplier() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(CoinSide.HEADS, mines=[0])
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.MINES,
        mines_count=1,
    )
    assert game.status is GameStatus.ACTIVE
    assert game.mines_tiles == [0]
    revealed = await harness.service.mines_reveal(game.game_id, "1", 1)
    assert revealed.status is GameStatus.ACTIVE
    assert revealed.mines_revealed == [1]
    cashed = await harness.service.mines_cashout(game.game_id, "1")
    assert cashed.status is GameStatus.COMPLETED
    assert cashed.winner_ids == ["1"]
    payout = mines_payout_lamports(50_000_000, 1, 1)
    profit = payout - 50_000_000
    assert cashed.winner_payout_lamports == payout
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000 + payout
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000 - profit
    payout_usd = Decimal("5") * Decimal(payout) / Decimal(50_000_000)
    assert cashed.player_profit_fiat("1") == payout_usd - Decimal("5")
    assert cashed.house_game_pnl_fiat() == Decimal("5") - payout_usd


@pytest.mark.asyncio
async def test_mines_hit_credits_house() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(CoinSide.HEADS, mines=[0])
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    start_house = harness.house_repo.record.sol_balance_lamports
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.MINES,
        mines_count=1,
    )
    assert harness.house_repo.record.sol_balance_lamports == start_house
    lost = await harness.service.mines_reveal(game.game_id, "1", 0)
    assert lost.status is GameStatus.COMPLETED
    assert lost.winner_ids == [HOUSE_USER_ID]
    assert lost.mines_hit == 0
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000
    assert harness.house_repo.record.sol_balance_lamports == start_house + 50_000_000
    assert lost.house_game_pnl_fiat() == Decimal("5")


@pytest.mark.asyncio
async def test_mines_rejects_over_ten_percent_house() -> None:
    harness = GameHarness()
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "20")
    with pytest.raises(GameError, match="10% of the house book"):
        await harness.service.create_game(
            player1_id="1",
            fiat_amount=Decimal("51"),
            side=CoinSide.HEADS,
            game_type=GameType.MINES,
            mines_count=3,
        )


@pytest.mark.asyncio
async def test_mines_starts_without_locking_full_board_payout() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(CoinSide.HEADS, mines=[0, 1, 2])
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.MINES,
        mines_count=3,
    )
    assert game.status is GameStatus.ACTIVE
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000


@pytest.mark.asyncio
async def test_mines_cashout_requires_a_gem() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(CoinSide.HEADS, mines=[0])
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.MINES,
        mines_count=1,
    )
    with pytest.raises(GameError, match="at least one gem"):
        await harness.service.mines_cashout(game.game_id, "1")
