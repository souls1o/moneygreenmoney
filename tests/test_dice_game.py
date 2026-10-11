from decimal import Decimal

import pytest

from casino.domain.enums import CoinSide, GameMode, GameStatus, GameType
from casino.errors import GameError
from casino.domain.records import GameRecord, is_house_seat
from tests.conftest import FixedRandomService
from tests.test_game_service import GameHarness


def test_dice_matchup_label_is_one_v_one_style() -> None:
    def game(max_players: int) -> GameRecord:
        return GameRecord(
            game_id="dc_1",
            status=GameStatus.PENDING,
            creator_id="1",
            bet_amount_lamports=1,
            currency="USD",
            bet_amount_fiat=Decimal("5"),
            game_type=GameType.DICE,
            max_players=max_players,
            heads_ids=["1"],
        )

    assert game(2).matchup_label() == "1v1"
    assert game(3).matchup_label() == "1v1v1"
    assert game(4).matchup_label() == "1v1v1v1"
    team = GameRecord(
        game_id="dc_2v2",
        status=GameStatus.PENDING,
        creator_id="1",
        bet_amount_lamports=1,
        currency="USD",
        bet_amount_fiat=Decimal("5"),
        game_type=GameType.DICE,
        mode=GameMode.TEAM,
        max_players=4,
        heads_ids=["1"],
    )
    assert team.matchup_label() == "2v2"
    assert team.next_vacant_side() is CoinSide.HEADS
    team.heads_ids = ["1", "HOUSE"]
    assert team.next_vacant_side() is CoinSide.TAILS


def test_call_bot_cap_allows_two_500_seats_not_a_third() -> None:
    game = GameRecord(
        game_id="dc_cap",
        status=GameStatus.PENDING,
        creator_id="1",
        bet_amount_lamports=1,
        currency="USD",
        bet_amount_fiat=Decimal("500"),
        game_type=GameType.DICE,
        max_players=4,
        heads_ids=["1"],
    )
    cap = Decimal("1000")
    seat = Decimal("500")
    assert game.can_call_house_seat(max_house_usd=cap, seat_usd=seat)
    game.house_seat_ids = ["HOUSE"]
    assert game.can_call_house_seat(max_house_usd=cap, seat_usd=seat)
    game.house_seat_ids = ["HOUSE", "HOUSE:2"]
    assert not game.can_call_house_seat(max_house_usd=cap, seat_usd=seat)


def _dice_harness(pairs: list[tuple[int, int]]) -> GameHarness:
    harness = GameHarness(CoinSide.HEADS)
    harness.service._rng = FixedRandomService(CoinSide.HEADS, dice_pairs=pairs)
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    return harness


async def _start_two_player_dice(harness: GameHarness, first_to: int = 1):
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.DICE,
        max_players=2,
        first_to=first_to,
    )
    await harness.service.join_game(game.game_id, "2")
    started = await harness.ready_all(game.game_id)
    harness.games_repo.games[started.game_id].private_channel_id = "dice-chan"
    started.private_channel_id = "dice-chan"
    return started


async def _drain_house_turns(harness: GameHarness, channel_id: str, game):
    events = []
    while game.status is GameStatus.ACTIVE:
        current = game.current_turn_id()
        if current is None or not is_house_seat(current):
            break
        result = await harness.service.play_next_house_turn(channel_id)
        events.extend(result.events)
        game = result.game
    return game, events


@pytest.mark.asyncio
async def test_dice_unique_high_roll_wins_the_round_and_match() -> None:
    harness = _dice_harness([(6, 6), (1, 2)])
    game = await _start_two_player_dice(harness, first_to=1)
    assert game.status is GameStatus.ACTIVE
    first = await harness.service.roll_dice("dice-chan", "1")
    assert first.game.status is GameStatus.ACTIVE
    assert first.game.score_for("1") == 0
    assert first.game.score_for("2") == 0
    second = await harness.service.roll_dice("dice-chan", "2")
    assert second.game.status is GameStatus.COMPLETED
    assert second.game.winner_ids == ["1"]
    assert second.events[-1].round_winner_id == "1"
    assert second.events[-1].match_over is True


@pytest.mark.asyncio
async def test_dice_round_tie_awards_no_point() -> None:
    harness = _dice_harness([(5, 5), (4, 6), (6, 6), (1, 1)])
    game = await _start_two_player_dice(harness, first_to=1)
    await harness.service.roll_dice("dice-chan", "1")
    tied = await harness.service.roll_dice("dice-chan", "2")
    assert tied.events[-1].round_tied is True
    assert tied.game.status is GameStatus.ACTIVE
    assert tied.game.score_for("1") == 0
    assert tied.game.score_for("2") == 0
    await harness.service.roll_dice("dice-chan", "1")
    finished = await harness.service.roll_dice("dice-chan", "2")
    assert finished.game.status is GameStatus.COMPLETED
    assert finished.game.winner_ids == ["1"]


@pytest.mark.asyncio
async def test_dice_rejects_rolls_out_of_turn() -> None:
    harness = _dice_harness([(6, 6), (1, 1)])
    await _start_two_player_dice(harness, first_to=1)
    with pytest.raises(GameError, match="It's not your turn"):
        await harness.service.roll_dice("dice-chan", "2")


@pytest.mark.asyncio
async def test_dice_call_bot_fills_and_auto_rolls() -> None:
    harness = _dice_harness([(6, 6), (1, 1)])
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.DICE,
        max_players=2,
        first_to=1,
    )
    seated = await harness.service.call_bot(game.game_id, "1")
    assert seated.house_seat_count() == 1
    started = await harness.service.mark_ready(game.game_id, "1")
    assert started.status is GameStatus.ACTIVE
    harness.games_repo.games[started.game_id].private_channel_id = "dice-chan"
    started.private_channel_id = "dice-chan"
    result = await harness.service.roll_dice("dice-chan", "1")
    assert all(not is_house_seat(event.roll.player_id) for event in result.events)
    assert is_house_seat(result.game.current_turn_id() or "")
    assert result.game.status is GameStatus.ACTIVE
    finished, house_events = await _drain_house_turns(harness, "dice-chan", result.game)
    assert any(is_house_seat(event.roll.player_id) for event in house_events)
    assert finished.status is GameStatus.COMPLETED
    assert finished.winner_ids == ["1"]


@pytest.mark.asyncio
async def test_dice_first_to_two_requires_two_round_wins() -> None:
    harness = _dice_harness([(6, 6), (1, 1), (6, 6), (1, 1)])
    game = await _start_two_player_dice(harness, first_to=2)
    await harness.service.roll_dice("dice-chan", "1")
    after_round = await harness.service.roll_dice("dice-chan", "2")
    assert after_round.game.status is GameStatus.ACTIVE
    assert after_round.game.score_for("1") == 1
    await harness.service.roll_dice("dice-chan", "1")
    finished = await harness.service.roll_dice("dice-chan", "2")
    assert finished.game.status is GameStatus.COMPLETED
    assert finished.game.winner_ids == ["1"]
    assert finished.game.score_for("1") == 2


async def _start_three_player_dice(harness: GameHarness, first_to: int = 1):
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    harness.seed_user("3", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.DICE,
        max_players=3,
        first_to=first_to,
    )
    await harness.service.join_game(game.game_id, "2")
    await harness.service.join_game(game.game_id, "3")
    started = await harness.ready_all(game.game_id)
    harness.games_repo.games[started.game_id].private_channel_id = "dice-chan"
    started.private_channel_id = "dice-chan"
    return started


@pytest.mark.asyncio
async def test_dice_three_player_ffa_awards_unique_high_total() -> None:
    harness = _dice_harness([(6, 6), (1, 2), (3, 3)])
    await _start_three_player_dice(harness, first_to=1)
    first = await harness.service.roll_dice("dice-chan", "1")
    assert first.events[0].roll.scores == {"1": 0, "2": 0, "3": 0}
    assert first.game.score_for("1") == 0
    second = await harness.service.roll_dice("dice-chan", "2")
    assert second.events[0].roll.scores == {"1": 0, "2": 0, "3": 0}
    assert second.game.score_for("1") == 0
    assert second.game.score_for("2") == 0
    finished = await harness.service.roll_dice("dice-chan", "3")
    assert finished.events[0].roll.scores == {"1": 1, "2": 0, "3": 0}
    assert finished.game.status is GameStatus.COMPLETED
    assert finished.game.winner_ids == ["1"]
    assert finished.game.house_fee_lamports == 4_500_000
    assert finished.game.winner_payout_lamports == 145_500_000


@pytest.mark.asyncio
async def test_dice_three_player_high_tie_is_a_roll_off() -> None:
    harness = _dice_harness([(6, 6), (1, 2), (6, 6), (6, 6), (1, 1)])
    await _start_three_player_dice(harness, first_to=1)
    await harness.service.roll_dice("dice-chan", "1")
    await harness.service.roll_dice("dice-chan", "2")
    tied = await harness.service.roll_dice("dice-chan", "3")
    assert tied.game.status is GameStatus.ACTIVE
    assert tied.game.score_for("1") == 0
    assert tied.game.score_for("2") == 0
    assert tied.game.score_for("3") == 0
    assert tied.events[-1].roll_off_ids == ("1", "3")
    assert tied.game.rolling_player_ids() == ["1", "3"]
    with pytest.raises(GameError, match="It's not your turn"):
        await harness.service.roll_dice("dice-chan", "2")
    with pytest.raises(GameError, match="It's not your turn"):
        await harness.service.roll_dice("dice-chan", "3")
    first_off = await harness.service.roll_dice("dice-chan", "1")
    assert first_off.game.score_for("1") == 0
    finished = await harness.service.roll_dice("dice-chan", "3")
    assert finished.game.status is GameStatus.COMPLETED
    assert finished.game.winner_ids == ["1"]
    assert finished.events[-1].round_winner_id == "1"


@pytest.mark.asyncio
async def test_dice_roll_off_can_narrow_and_continue() -> None:
    harness = _dice_harness([(6, 6), (6, 6), (6, 6), (6, 6), (6, 6), (3, 3), (6, 6), (1, 1)])
    await _start_three_player_dice(harness, first_to=1)
    await harness.service.roll_dice("dice-chan", "1")
    await harness.service.roll_dice("dice-chan", "2")
    first_tie = await harness.service.roll_dice("dice-chan", "3")
    assert first_tie.events[-1].roll_off_ids == ("1", "2", "3")
    await harness.service.roll_dice("dice-chan", "1")
    await harness.service.roll_dice("dice-chan", "2")
    second_tie = await harness.service.roll_dice("dice-chan", "3")
    assert second_tie.game.status is GameStatus.ACTIVE
    assert second_tie.game.score_for("1") == 0
    assert second_tie.events[-1].roll_off_ids == ("1", "2")
    await harness.service.roll_dice("dice-chan", "1")
    finished = await harness.service.roll_dice("dice-chan", "2")
    assert finished.game.status is GameStatus.COMPLETED
    assert finished.game.winner_ids == ["1"]


@pytest.mark.asyncio
async def test_dice_call_bot_fills_four_player_table() -> None:
    harness = _dice_harness([(6, 6), (1, 1), (2, 2), (3, 3)])
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.DICE,
        max_players=4,
        first_to=1,
    )
    await harness.service.call_bot(game.game_id, "1")
    await harness.service.call_bot(game.game_id, "1")
    filled = await harness.service.call_bot(game.game_id, "1")
    assert filled.house_seat_count() == 3
    assert filled.is_full()
    started = await harness.service.mark_ready(game.game_id, "1")
    assert started.status is GameStatus.ACTIVE
    harness.games_repo.games[started.game_id].private_channel_id = "dice-chan"
    result = await harness.service.roll_dice("dice-chan", "1")
    assert result.game.status is GameStatus.ACTIVE
    finished, house_events = await _drain_house_turns(harness, "dice-chan", result.game)
    events = [*result.events, *house_events]
    assert finished.status is GameStatus.COMPLETED
    assert finished.winner_ids == ["1"]
    assert len(events) == 4
    for event in events[:-1]:
        assert all(score == 0 for score in event.roll.scores.values())
    assert events[-1].roll.scores["1"] == 1


@pytest.mark.asyncio
async def test_dice_human_cannot_skip_intervening_house_seat() -> None:
    harness = _dice_harness([(6, 6), (1, 1), (5, 5), (2, 2)])
    harness.seed_user("1", "10")
    harness.seed_user("3", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.DICE,
        max_players=4,
        first_to=1,
    )
    await harness.service.call_bot(game.game_id, "1")
    await harness.service.join_game(game.game_id, "3")
    seated = await harness.service.call_bot(game.game_id, "1")
    assert seated.player_ids() == ["1", "HOUSE", "3", "HOUSE:2"]
    started = await harness.ready_all(game.game_id)
    harness.games_repo.games[started.game_id].private_channel_id = "dice-chan"
    first = await harness.service.roll_dice("dice-chan", "1")
    assert first.game.current_turn_id() == "HOUSE"
    with pytest.raises(GameError, match="It's not your turn"):
        await harness.service.roll_dice("dice-chan", "3")
    house = await harness.service.play_next_house_turn("dice-chan")
    assert is_house_seat(house.events[0].roll.player_id)
    assert house.game.current_turn_id() == "3"
    third = await harness.service.roll_dice("dice-chan", "3")
    assert third.game.current_turn_id() == "HOUSE:2"
    with pytest.raises(GameError, match="It's not your turn"):
        await harness.service.roll_dice("dice-chan", "1")
    finished = await harness.service.play_next_house_turn("dice-chan")
    assert finished.game.status is GameStatus.COMPLETED
    assert finished.game.winner_ids == ["1"]


async def _start_dice_2v2(harness: GameHarness, first_to: int = 1):
    for player_id in ("1", "2", "3", "4"):
        harness.seed_user(player_id, "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        mode=GameMode.TEAM,
        game_type=GameType.DICE,
        max_players=4,
        first_to=first_to,
    )
    await harness.service.join_game(game.game_id, "2", CoinSide.HEADS)
    await harness.service.join_game(game.game_id, "3", CoinSide.TAILS)
    seated = await harness.service.join_game(game.game_id, "4", CoinSide.TAILS)
    assert seated.heads_ids == ["1", "2"]
    assert seated.tails_ids == ["3", "4"]
    assert seated.matchup_label() == "2v2"
    started = await harness.ready_all(game.game_id)
    harness.games_repo.games[started.game_id].private_channel_id = "dice-chan"
    started.private_channel_id = "dice-chan"
    return started


@pytest.mark.asyncio
async def test_dice_2v2_highest_roll_scores_the_team_and_splits_the_pot() -> None:
    harness = _dice_harness([(6, 6), (1, 1), (2, 2), (3, 3)])
    await _start_dice_2v2(harness, first_to=1)
    await harness.service.roll_dice("dice-chan", "1")
    await harness.service.roll_dice("dice-chan", "2")
    await harness.service.roll_dice("dice-chan", "3")
    finished = await harness.service.roll_dice("dice-chan", "4")
    assert finished.events[-1].round_winner_id == "1"
    assert finished.game.score_for("1") == 1
    assert finished.game.score_for("2") == 1
    assert finished.game.score_for("3") == 0
    assert finished.game.score_for("4") == 0
    assert finished.game.status is GameStatus.COMPLETED
    assert finished.game.winner_ids == ["1", "2"]
    assert finished.game.house_fee_lamports == 6_000_000
    assert finished.game.per_winner_payout_lamports == 97_000_000


@pytest.mark.asyncio
async def test_dice_2v2_same_team_high_tie_awards_the_team() -> None:
    harness = _dice_harness([(6, 6), (6, 6), (1, 1), (1, 1)])
    await _start_dice_2v2(harness, first_to=1)
    await harness.service.roll_dice("dice-chan", "1")
    await harness.service.roll_dice("dice-chan", "2")
    await harness.service.roll_dice("dice-chan", "3")
    finished = await harness.service.roll_dice("dice-chan", "4")
    assert finished.events[-1].round_tied is False
    assert finished.events[-1].roll_off_ids == ()
    assert finished.game.winner_ids == ["1", "2"]


@pytest.mark.asyncio
async def test_dice_2v2_cross_team_high_tie_is_a_roll_off() -> None:
    harness = _dice_harness([(6, 6), (1, 1), (6, 6), (2, 2), (6, 6), (1, 1)])
    await _start_dice_2v2(harness, first_to=1)
    await harness.service.roll_dice("dice-chan", "1")
    await harness.service.roll_dice("dice-chan", "2")
    await harness.service.roll_dice("dice-chan", "3")
    tied = await harness.service.roll_dice("dice-chan", "4")
    assert tied.game.status is GameStatus.ACTIVE
    assert tied.events[-1].roll_off_ids == ("1", "3")
    with pytest.raises(GameError, match="It's not your turn"):
        await harness.service.roll_dice("dice-chan", "2")
    await harness.service.roll_dice("dice-chan", "1")
    finished = await harness.service.roll_dice("dice-chan", "3")
    assert finished.game.status is GameStatus.COMPLETED
    assert finished.game.winner_ids == ["1", "2"]


@pytest.mark.asyncio
async def test_dice_2v2_call_bot_fills_from_top_to_bottom() -> None:
    harness = _dice_harness([(6, 6), (1, 1), (2, 2), (3, 3)])
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        mode=GameMode.TEAM,
        game_type=GameType.DICE,
        max_players=4,
        first_to=1,
    )
    seated = await harness.service.call_bot(game.game_id, "1")
    assert seated.heads_ids == ["1", "HOUSE"]
    assert seated.tails_ids == []
    opponent = await harness.service.join_game(game.game_id, "2", CoinSide.TAILS)
    assert opponent.tails_ids == ["2"]
    filled = await harness.service.call_bot(game.game_id, "1")
    assert filled.heads_ids == ["1", "HOUSE"]
    assert filled.tails_ids == ["2", "HOUSE:2"]
    assert filled.is_full()
    started = await harness.ready_all(game.game_id)
    harness.games_repo.games[started.game_id].private_channel_id = "dice-chan"
    first = await harness.service.roll_dice("dice-chan", "1")
    after_house, _events = await _drain_house_turns(harness, "dice-chan", first.game)
    second = await harness.service.roll_dice("dice-chan", "2")
    finished, _more = await _drain_house_turns(harness, "dice-chan", second.game)
    assert after_house.status is GameStatus.ACTIVE
    assert finished.status is GameStatus.COMPLETED
    assert finished.winner_ids == ["1", "HOUSE"]
