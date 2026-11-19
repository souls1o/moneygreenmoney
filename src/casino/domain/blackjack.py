from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

RANK_LABELS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUIT_LABELS = ("♠", "♥", "♦", "♣")
BLACKJACK_DECKS = 8
BLACKJACK_SHOE_SIZE = 52 * BLACKJACK_DECKS


class BlackjackAction(StrEnum):
    HIT = "hit"
    STAND = "stand"
    SPLIT = "split"
    DOUBLE = "double"


class BlackjackOutcome(StrEnum):
    WIN = "win"
    LOSE = "lose"
    PUSH = "push"
    BLACKJACK = "blackjack"


@dataclass(frozen=True, slots=True)
class BlackjackCard:
    rank: int
    suit: int

    def __post_init__(self) -> None:
        if self.rank < 0 or self.rank > 12 or self.suit < 0 or self.suit > 3:
            raise ValueError("Invalid blackjack card.")

    @property
    def label(self) -> str:
        return f"{RANK_LABELS[self.rank]}{SUIT_LABELS[self.suit]}"

    @property
    def pip_value(self) -> int:
        if self.rank == 0:
            return 11
        if self.rank >= 9:
            return 10
        return self.rank + 1

    @property
    def is_ace(self) -> bool:
        return self.rank == 0


def card_from_index(index: int) -> BlackjackCard:
    value = int(index) % 52
    return BlackjackCard(rank=value % 13, suit=value // 13)


def hand_total(cards: list[BlackjackCard]) -> int:
    total = 0
    aces = 0
    for card in cards:
        if card.is_ace:
            aces += 1
            total += 11
        else:
            total += card.pip_value
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def is_soft(cards: list[BlackjackCard]) -> bool:
    total = 0
    aces = 0
    for card in cards:
        if card.is_ace:
            aces += 1
            total += 11
        else:
            total += card.pip_value
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return aces > 0 and total <= 21


def is_bust(cards: list[BlackjackCard]) -> bool:
    return hand_total(cards) > 21


def is_natural_blackjack(cards: list[BlackjackCard], *, from_split: bool = False) -> bool:
    return not from_split and len(cards) == 2 and hand_total(cards) == 21


def dealer_should_hit(cards: list[BlackjackCard]) -> bool:
    total = hand_total(cards)
    if total < 17:
        return True
    return total == 17 and is_soft(cards)


def compare_hands(
    player_cards: list[BlackjackCard],
    dealer_cards: list[BlackjackCard],
    *,
    from_split: bool = False,
) -> BlackjackOutcome:
    player_bj = is_natural_blackjack(player_cards, from_split=from_split)
    dealer_bj = is_natural_blackjack(dealer_cards)
    player_bust = is_bust(player_cards)
    dealer_bust = is_bust(dealer_cards)
    if player_bust:
        return BlackjackOutcome.LOSE
    if player_bj and dealer_bj:
        return BlackjackOutcome.PUSH
    if player_bj:
        return BlackjackOutcome.BLACKJACK
    if dealer_bj:
        return BlackjackOutcome.LOSE
    if dealer_bust:
        return BlackjackOutcome.WIN
    player_total = hand_total(player_cards)
    dealer_total = hand_total(dealer_cards)
    if player_total > dealer_total:
        return BlackjackOutcome.WIN
    if player_total < dealer_total:
        return BlackjackOutcome.LOSE
    return BlackjackOutcome.PUSH


@dataclass(slots=True)
class BlackjackHand:
    cards: list[BlackjackCard] = field(default_factory=list)
    bet_lamports: int = 0
    stood: bool = False
    doubled: bool = False
    from_split: bool = False
    from_split_aces: bool = False
    outcome: BlackjackOutcome | None = None

    def total(self) -> int:
        return hand_total(self.cards)

    def is_bust(self) -> bool:
        return is_bust(self.cards)

    def is_blackjack(self) -> bool:
        return is_natural_blackjack(self.cards, from_split=self.from_split)

    def is_done(self) -> bool:
        if self.stood or self.doubled or self.is_bust() or self.is_blackjack():
            return True
        if self.from_split_aces and len(self.cards) >= 2:
            return True
        return False

    def can_hit(self) -> bool:
        if self.is_done():
            return False
        if self.from_split_aces:
            return False
        return True

    def can_stand(self) -> bool:
        return not self.is_done()

    def can_double(self) -> bool:
        if self.is_done() or len(self.cards) != 2:
            return False
        if self.from_split or self.from_split_aces:
            return False
        return True


def can_split(hands: list[BlackjackHand], hand_index: int) -> bool:
    if len(hands) != 1 or hand_index != 0:
        return False
    hand = hands[hand_index]
    if hand.is_done() or len(hand.cards) != 2:
        return False
    return hand.cards[0].rank == hand.cards[1].rank


def legal_actions(hands: list[BlackjackHand], hand_index: int) -> frozenset[BlackjackAction]:
    if not hands or hand_index < 0 or hand_index >= len(hands):
        return frozenset()
    hand = hands[hand_index]
    if all(item.is_done() for item in hands):
        return frozenset()
    if hand.is_done():
        return frozenset()
    allowed: set[BlackjackAction] = set()
    if hand.can_hit():
        allowed.add(BlackjackAction.HIT)
    if hand.can_stand():
        allowed.add(BlackjackAction.STAND)
    if hand.can_double():
        allowed.add(BlackjackAction.DOUBLE)
    if can_split(hands, hand_index):
        allowed.add(BlackjackAction.SPLIT)
    return frozenset(allowed)


def advance_hand_index(hands: list[BlackjackHand], hand_index: int) -> int:
    index = hand_index
    while index < len(hands) and hands[index].is_done():
        index += 1
    return min(index, max(len(hands) - 1, 0))


def player_finished(hands: list[BlackjackHand]) -> bool:
    return bool(hands) and all(hand.is_done() for hand in hands)
