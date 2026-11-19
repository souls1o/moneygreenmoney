from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.enums import CoinSide, GameStatus, GameType, LedgerEntryType
from casino.domain.records import HOUSE_USER_ID
from casino.domain.target import closest_winners, parse_target_pick, target_draw_frames, target_draw_interval
from casino.errors import GameError
from casino.utils.rng import ProvablyFairService
from tests.conftest import FixedRandomService
from tests.test_game_service import GameHarness


def test_parse_target_pick_bounds() -> None:
    assert parse_target_pick("1") == 1
    assert parse_target_pick(999) == 999
    with pytest.raises(GameError):
        parse_target_pick(0)
    with pytest.raises(GameError):
        parse_target_pick(1000)
    with pytest.raises(GameError):
        parse_target_pick("nope")


def test_closest_winners_and_push() -> None:
    assert closest_winners({"1": 10, "2": 90}, 20) == ["1"]
    assert closest_winners({"1": 10, HOUSE_USER_ID: 30}, 20) == []
    assert closest_winners({"1": 50, "2": 50}, 80) == []
    assert closest_winners({"1": 10, "2": 30, "3": 90}, 20) == ["1", "2"]


def test_target_draw_counts_one_by_one_when_small() -> None:
    assert target_draw_frames(1) == [1]
    assert target_draw_frames(5) == [1, 2, 3, 4, 5]
    assert target_draw_interval(5) == pytest.approx(1.25)


def test_target_draw_climbs_linearly_in_five_seconds() -> None:
    frames = target_draw_frames(400)
    assert frames[0] == 1
    assert frames[-1] == 400
    assert frames == sorted(set(frames))
    assert len(frames) <= 20
    assert target_draw_interval(len(frames)) == pytest.approx(5.0 / (len(frames) - 1))
    mid = frames[len(frames) // 2]
    assert 1 < mid < 400


def test_hmac_target_is_one_to_999() -> None:
    rng = ProvablyFairService()
    for index in range(20):
        fair = rng.resolve_target("server", "client", 1, index)
        assert 1 <= fair.value <= 999


def _harness(targets: list[int]) -> GameHarness:
    harness = GameHarness(CoinSide.HEADS)
    harness.service._rng = FixedRandomService(CoinSide.HEADS, targets=targets)
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    return harness


@pytest.mark.asyncio
async def test_target_pvp_closest_wins() -> None:
    harness = _harness([40, 10])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.TARGET,
    )
    await harness.service.lock_target_pick(game.game_id, "1", 10)
    await harness.service.join_game(game.game_id, "2")
    resolved = await harness.service.lock_target_pick(game.game_id, "2", 90)
    assert resolved.status is GameStatus.COMPLETED
    assert resolved.target_number == 40
    assert resolved.winner_ids == ["1"]
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000 + 97_000_000
    assert harness.users_repo.users["2"].sol_balance_lamports == 10_000_000_000 - 50_000_000


@pytest.mark.asyncio
async def test_target_tie_refunds() -> None:
    harness = _harness([50])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.TARGET,
    )
    await harness.service.lock_target_pick(game.game_id, "1", 40)
    await harness.service.join_game(game.game_id, "2")
    resolved = await harness.service.lock_target_pick(game.game_id, "2", 60)
    assert resolved.winner_ids == []
    refunds = [entry for entry in harness.ledger_repo.entries if entry.type is LedgerEntryType.REFUND]
    assert len(refunds) == 2
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000
    assert harness.users_repo.users["2"].sol_balance_lamports == 10_000_000_000


@pytest.mark.asyncio
async def test_target_vs_house() -> None:
    harness = _harness([100, 200])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.TARGET,
    )
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.service.lock_target_pick(game.game_id, "1", 90)
    assert resolved.status is GameStatus.COMPLETED
    assert resolved.target_number == 100
    assert resolved.target_picks[HOUSE_USER_ID] == 200
    assert resolved.winner_ids == ["1"]
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000 - 50_000_000 + 3_000_000


@pytest.mark.asyncio
async def test_target_lock_does_not_resolve_until_table_is_full() -> None:
    harness = _harness([40])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.TARGET,
    )
    locked = await harness.service.lock_target_pick(game.game_id, "1", 10)
    assert locked.status is GameStatus.PENDING
    assert locked.target_number is None
    assert locked.has_target_pick("1")
    assert not locked.has_target_pick("2")


@pytest.mark.asyncio
async def test_target_cannot_relock() -> None:
    harness = _harness([40])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.TARGET,
    )
    await harness.service.lock_target_pick(game.game_id, "1", 10)
    with pytest.raises(GameError, match="already locked"):
        await harness.service.lock_target_pick(game.game_id, "1", 20)


@pytest.mark.asyncio
async def test_target_three_player_split() -> None:
    harness = _harness([20])
    harness.seed_user("3", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.TARGET,
        max_players=3,
    )
    await harness.service.lock_target_pick(game.game_id, "1", 10)
    await harness.service.join_game(game.game_id, "2")
    await harness.service.lock_target_pick(game.game_id, "2", 30)
    await harness.service.join_game(game.game_id, "3")
    resolved = await harness.service.lock_target_pick(game.game_id, "3", 90)
    assert resolved.status is GameStatus.COMPLETED
    assert set(resolved.winner_ids) == {"1", "2"}
    assert harness.users_repo.users["3"].sol_balance_lamports == 10_000_000_000 - 50_000_000
