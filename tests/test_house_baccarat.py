from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.baccarat import BaccaratDeal, BaccaratSide, baccarat_deal_frames, parse_baccarat_side
from casino.domain.blackjack import BlackjackCard
from casino.domain.enums import CoinSide, GameStatus, GameType
from casino.domain.records import HOUSE_USER_ID
from casino.errors import GameError, InsufficientBalanceError
from tests.conftest import FixedRandomService
from tests.test_game_service import GameHarness


def test_parse_baccarat_side() -> None:
    assert parse_baccarat_side("player") is BaccaratSide.PLAYER
    with pytest.raises(GameError):
        parse_baccarat_side("red")


def test_baccarat_deal_frames_follow_draw_order() -> None:
    deal = BaccaratDeal(
        player_cards=(
            BlackjackCard(rank=8, suit=0),
            BlackjackCard(rank=9, suit=1),
            BlackjackCard(rank=4, suit=2),
        ),
        banker_cards=(
            BlackjackCard(rank=5, suit=0),
            BlackjackCard(rank=6, suit=1),
        ),
    )
    frames = baccarat_deal_frames(deal)
    assert len(frames) == 5
    assert len(frames[0][0]) == 1 and len(frames[0][1]) == 0
    assert len(frames[3][0]) == 2 and len(frames[3][1]) == 2
    assert len(frames[-1][0]) == 3 and len(frames[-1][1]) == 2


@pytest.mark.asyncio
async def test_house_baccarat_opens_without_debiting() -> None:
    harness = GameHarness()
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BACCARAT,
    )
    assert game.status is GameStatus.ACTIVE
    assert game.baccarat_side is None
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000


@pytest.mark.asyncio
async def test_house_baccarat_player_win_pays_even_money() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(
        CoinSide.HEADS,
        cards=[
            BlackjackCard(rank=8, suit=0),
            BlackjackCard(rank=9, suit=1),
            BlackjackCard(rank=9, suit=2),
            BlackjackCard(rank=9, suit=3),
        ],
    )
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BACCARAT,
    )
    played = await harness.service.play_baccarat(game.game_id, "1", BaccaratSide.PLAYER)
    assert played.status is GameStatus.COMPLETED
    assert played.winner_ids == ["1"]
    assert played.winner_payout_lamports == 100_000_000
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000 + 100_000_000
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000 - 50_000_000


@pytest.mark.asyncio
async def test_house_baccarat_player_loss_credits_house() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(
        CoinSide.HEADS,
        cards=[
            BlackjackCard(rank=9, suit=0),
            BlackjackCard(rank=8, suit=1),
            BlackjackCard(rank=9, suit=2),
            BlackjackCard(rank=9, suit=3),
        ],
    )
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BACCARAT,
    )
    played = await harness.service.play_baccarat(game.game_id, "1", BaccaratSide.PLAYER)
    assert played.winner_ids == [HOUSE_USER_ID]
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000 + 50_000_000


@pytest.mark.asyncio
async def test_house_baccarat_rejects_broke_player_on_side_click() -> None:
    harness = GameHarness()
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BACCARAT,
    )
    harness.users_repo.users["1"].sol_balance_lamports = 0
    with pytest.raises(InsufficientBalanceError):
        await harness.service.play_baccarat(game.game_id, "1", BaccaratSide.PLAYER)
    current = await harness.service.get(game.game_id)
    assert current.status is GameStatus.ACTIVE
    assert current.baccarat_side is None
