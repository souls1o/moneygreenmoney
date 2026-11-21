from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.blackjack import (
    BlackjackAction,
    BlackjackCard,
    BlackjackHand,
    BlackjackOutcome,
    can_split,
    card_from_index,
    compare_hands,
    dealer_should_hit,
    hand_total,
    is_natural_blackjack,
    is_soft,
    legal_actions,
)
from casino.domain.enums import CoinSide, GameStatus, GameType, LedgerEntryType
from casino.domain.records import HOUSE_USER_ID
from casino.errors import GameError
from casino.utils.rng import ProvablyFairService
from tests.conftest import FixedRandomService
from tests.test_game_service import GameHarness

T = BlackjackCard(rank=9, suit=0)
SIX = BlackjackCard(rank=5, suit=1)
NINE = BlackjackCard(rank=8, suit=2)
FIVE = BlackjackCard(rank=4, suit=3)
ACE = BlackjackCard(rank=0, suit=0)
SEVEN = BlackjackCard(rank=6, suit=0)
EIGHT = BlackjackCard(rank=7, suit=0)


def test_hand_total_and_soft_ace() -> None:
    assert hand_total([ACE, T]) == 21
    assert is_natural_blackjack([ACE, T]) is True
    assert is_soft([ACE, SIX]) is True
    assert hand_total([ACE, ACE, NINE]) == 21
    assert hand_total([T, T, FIVE]) == 25


def test_blackjack_is_not_two_tens() -> None:
    assert is_natural_blackjack([T, T]) is False
    assert is_natural_blackjack([ACE, T], from_split=True) is False


def test_dealer_hits_soft_seventeen_stands_hard() -> None:
    assert dealer_should_hit([ACE, SIX]) is True
    assert dealer_should_hit([T, SEVEN]) is False
    assert dealer_should_hit([T, SIX]) is True
    assert dealer_should_hit([T, SIX, ACE]) is False
    assert dealer_should_hit([ACE, ACE, FIVE]) is True


def test_compare_hands_bust_blackjack_push() -> None:
    assert compare_hands([T, T, FIVE], [SIX, T]) is BlackjackOutcome.LOSE
    assert compare_hands([ACE, T], [SIX, NINE]) is BlackjackOutcome.BLACKJACK
    assert compare_hands([ACE, T], [ACE, T]) is BlackjackOutcome.PUSH
    assert compare_hands([T, NINE], [T, NINE]) is BlackjackOutcome.PUSH
    assert compare_hands([T, NINE], [T, T, FIVE]) is BlackjackOutcome.WIN


def test_no_double_after_split() -> None:
    first = BlackjackHand(cards=[EIGHT, T], bet_lamports=1, from_split=True)
    second = BlackjackHand(cards=[EIGHT, NINE], bet_lamports=1, from_split=True)
    assert can_split([first, second], 0) is False
    assert BlackjackAction.DOUBLE not in legal_actions([first, second], 0)
    assert BlackjackAction.DOUBLE not in legal_actions([first, second], 1)


def test_split_and_double_legality() -> None:
    pair = BlackjackHand(cards=[EIGHT, EIGHT], bet_lamports=1)
    assert BlackjackAction.SPLIT in legal_actions([pair], 0)
    assert can_split([pair], 0) is True
    eleven = BlackjackHand(cards=[ACE, T], bet_lamports=1)
    assert eleven.is_blackjack() is True
    assert legal_actions([eleven], 0) == frozenset()
    two = BlackjackHand(cards=[FIVE, SIX], bet_lamports=1)
    assert BlackjackAction.DOUBLE in legal_actions([two], 0)
    two.cards.append(T)
    assert BlackjackAction.DOUBLE not in legal_actions([two], 0)


def test_card_label_uses_unicode_suits() -> None:
    assert BlackjackCard(rank=0, suit=0).label == "A♠"
    assert BlackjackCard(rank=9, suit=1).label == "10♥"
    assert BlackjackCard(rank=12, suit=2).label == "K♦"
    assert BlackjackCard(rank=1, suit=3).label == "2♣"


def test_hmac_card_is_in_deck() -> None:
    rng = ProvablyFairService()
    card = rng.resolve_card("server", "client", 1, 0).card
    assert 0 <= card.rank <= 12
    assert 0 <= card.suit <= 3
    assert card_from_index(51).rank == 12
    assert card_from_index(51).suit == 3
    last = rng.resolve_card("server", "client", 1, 415).card
    assert 0 <= last.rank <= 12


def _harness(cards: list[BlackjackCard]) -> GameHarness:
    harness = GameHarness(CoinSide.HEADS)
    harness.service._rng = FixedRandomService(CoinSide.HEADS, cards=cards)
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    return harness


@pytest.mark.asyncio
async def test_create_blackjack_seats_house_and_deals() -> None:
    harness = _harness([T, SIX, NINE, FIVE])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    assert game.status is GameStatus.ACTIVE
    assert game.heads_ids == ["1"]
    assert game.tails_ids == [HOUSE_USER_ID]
    assert game.house_seat_ids == [HOUSE_USER_ID]
    assert len(game.blackjack_player_hands[0].cards) == 2
    assert len(game.blackjack_dealer_cards) == 2
    assert game.blackjack_hole_hidden is True
    assert BlackjackAction.HIT in game.blackjack_legal_actions()
    user = harness.users_repo.users["1"]
    assert user.sol_balance_lamports == 10_000_000_000 - 50_000_000
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000 - 50_000_000


@pytest.mark.asyncio
async def test_abort_unresolved_blackjack_refunds_player_and_house() -> None:
    harness = _harness([T, SIX, NINE, FIVE])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    aborted = await harness.service.abort_unresolved_vs_house(game.game_id, reason="table update failed")
    assert aborted.status is GameStatus.CANCELLED
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000


@pytest.mark.asyncio
async def test_hit_bust_pays_house() -> None:
    harness = _harness([T, SIX, NINE, FIVE, T])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    resolved = await harness.service.blackjack_action(game.game_id, "1", BlackjackAction.HIT)
    assert resolved.status is GameStatus.COMPLETED
    assert resolved.winner_ids == [HOUSE_USER_ID]
    assert resolved.blackjack_player_hands[0].is_bust()
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000 + 50_000_000


@pytest.mark.asyncio
async def test_stand_then_dealer_plays() -> None:
    harness = _harness([T, SIX, NINE, T, T])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    resolved = await harness.service.blackjack_action(game.game_id, "1", BlackjackAction.STAND)
    assert resolved.status is GameStatus.COMPLETED
    assert resolved.blackjack_hole_hidden is False
    assert resolved.winner_ids == ["1"]
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000 + 100_000_000
    assert resolved.house_fee_lamports == 0


@pytest.mark.asyncio
async def test_dealer_hits_soft_seventeen() -> None:
    harness = _harness([T, ACE, T, SIX, T])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    resolved = await harness.service.blackjack_action(game.game_id, "1", BlackjackAction.STAND)
    dealer = resolved.blackjack_dealer_cards
    assert [card.rank for card in dealer] == [ACE.rank, SIX.rank, T.rank]
    assert resolved.winner_ids == ["1"]


@pytest.mark.asyncio
async def test_push_refunds_both_sides() -> None:
    harness = _harness([T, T, T, T])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    resolved = await harness.service.blackjack_action(game.game_id, "1", BlackjackAction.STAND)
    assert resolved.winner_ids == []
    refunds = [entry for entry in harness.ledger_repo.entries if entry.type is LedgerEntryType.REFUND]
    assert len(refunds) == 2
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000


@pytest.mark.asyncio
async def test_natural_blackjack_settles_on_deal() -> None:
    harness = _harness([ACE, SIX, T, FIVE])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    assert game.status is GameStatus.COMPLETED
    assert game.winner_ids == ["1"]
    assert game.blackjack_player_hands[0].outcome is BlackjackOutcome.BLACKJACK
    assert game.house_fee_lamports == 0
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 50_000_000 + 125_000_000
    assert harness.house_repo.record.sol_balance_lamports == 5_000_000_000 - 75_000_000


@pytest.mark.asyncio
async def test_no_peek_lets_player_act_against_dealer_blackjack() -> None:
    harness = _harness([T, ACE, NINE, T, FIVE])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    assert game.status is GameStatus.ACTIVE
    assert is_natural_blackjack(game.blackjack_dealer_cards) is True
    assert BlackjackAction.HIT in game.blackjack_legal_actions()


@pytest.mark.asyncio
async def test_split_debits_extra() -> None:
    harness = _harness([EIGHT, SIX, EIGHT, FIVE, T, NINE])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    split = await harness.service.blackjack_action(game.game_id, "1", BlackjackAction.SPLIT)
    assert len(split.blackjack_player_hands) == 2
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 100_000_000
    assert split.wager_for("1").lamports == 100_000_000
    assert split.wager_for(HOUSE_USER_ID).lamports == 100_000_000


@pytest.mark.asyncio
async def test_double_debits_extra_and_stands() -> None:
    harness = _harness([FIVE, SIX, SIX, FIVE, T, NINE])
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    resolved = await harness.service.blackjack_action(game.game_id, "1", BlackjackAction.DOUBLE)
    assert resolved.status is GameStatus.COMPLETED
    assert resolved.blackjack_player_hands[0].doubled is True
    assert len(resolved.blackjack_player_hands[0].cards) == 3
    assert harness.users_repo.users["1"].sol_balance_lamports == 10_000_000_000 - 100_000_000 + 200_000_000


@pytest.mark.asyncio
async def test_non_owner_cannot_act() -> None:
    harness = _harness([T, SIX, NINE, FIVE])
    harness.seed_user("2", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.BLACKJACK,
    )
    with pytest.raises(GameError, match="Only the player"):
        await harness.service.blackjack_action(game.game_id, "2", BlackjackAction.HIT)


@pytest.mark.asyncio
async def test_house_cap_blocks_oversized_blackjack() -> None:
    harness = _harness([T, SIX, NINE, FIVE])
    harness.seed_user("1", "20")
    with pytest.raises(GameError, match="10% of the house book"):
        await harness.service.create_game(
            player1_id="1",
            fiat_amount=Decimal("51"),
            side=CoinSide.HEADS,
            game_type=GameType.BLACKJACK,
        )

