from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.enums import CoinSide, GameStatus, GameType
from casino.domain.limbo import (
    LIMBO_CRASH_SECONDS,
    crash_from_digest,
    limbo_crash_at,
    limbo_crash_frames,
    limbo_win_chance,
    parse_limbo_multiplier,
)
from casino.domain.records import HOUSE_USER_ID
from casino.errors import GameError
from casino.utils.rng import ProvablyFairService
from tests.conftest import FixedRandomService
from tests.test_game_service import GameHarness


def test_parse_limbo_multiplier_and_chance() -> None:
    assert parse_limbo_multiplier("2") == Decimal("2.00")
    assert parse_limbo_multiplier("2.5x") == Decimal("2.50")
    assert limbo_win_chance(Decimal("2")) == Decimal("48.5000")
    with pytest.raises(GameError):
        parse_limbo_multiplier("1")
    with pytest.raises(GameError):
        parse_limbo_multiplier("0.5")


def test_limbo_crash_climbs_exponentially() -> None:
    assert limbo_crash_frames(Decimal("1.00")) == [Decimal("1.00")]
    assert limbo_crash_at(0, Decimal("10")) == Decimal("1.00")
    assert limbo_crash_at(1, Decimal("10")) == Decimal("10.00")
    frames = limbo_crash_frames(Decimal("10"))
    assert frames[0] == Decimal("1.00")
    assert frames[-1] == Decimal("10.00")
    assert frames == sorted(set(frames))
    mid = limbo_crash_at(0.5, Decimal("10"))
    assert Decimal("1.00") < mid < Decimal("10.00")
    assert LIMBO_CRASH_SECONDS == 5.0


def test_hmac_limbo_is_at_least_one() -> None:
    rng = ProvablyFairService()
    result = rng.resolve_limbo("server", "client", 1)
    assert result.crash >= Decimal("1.00")
    assert crash_from_digest(result.digest) == result.crash


@pytest.mark.asyncio
async def test_limbo_win_pays_target_without_pot_fee() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(CoinSide.HEADS, limbos=[Decimal("2.50")])
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.LIMBO,
        limbo_target=Decimal("2"),
    )
    assert game.status is GameStatus.COMPLETED
    assert game.winner_ids == ["1"]
    assert game.house_fee_lamports == 0
    assert game.winner_payout_lamports == 100_000_000
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000 + 100_000_000
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000 - 50_000_000


@pytest.mark.asyncio
async def test_limbo_loss_credits_house_the_wager() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(CoinSide.HEADS, limbos=[Decimal("1.50")])
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.LIMBO,
        limbo_target=Decimal("2"),
    )
    assert game.winner_ids == [HOUSE_USER_ID]
    assert game.limbo_result == Decimal("1.50")
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000 + 50_000_000


@pytest.mark.asyncio
async def test_limbo_rejects_over_house_exposure() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(CoinSide.HEADS, limbos=[Decimal("2")])
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "20")
    with pytest.raises(GameError, match="enough SOL"):
        await harness.service.create_game(
            player1_id="1",
            fiat_amount=Decimal("5"),
            side=CoinSide.HEADS,
            game_type=GameType.LIMBO,
            limbo_target=Decimal("300"),
        )


@pytest.mark.asyncio
async def test_limbo_rejects_bet_over_ten_percent_of_house() -> None:
    harness = GameHarness()
    harness.service._rng = FixedRandomService(CoinSide.HEADS, limbos=[Decimal("2")])
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "20")
    with pytest.raises(GameError, match="10% of the house book"):
        await harness.service.create_game(
            player1_id="1",
            fiat_amount=Decimal("51"),
            side=CoinSide.HEADS,
            game_type=GameType.LIMBO,
            limbo_target=Decimal("2"),
        )
