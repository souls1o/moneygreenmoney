from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from casino.domain.blackjack import BlackjackCard
from casino.errors import GameError

BACCARAT_SECONDS = 30
BACCARAT_REVEAL_SECONDS = 10
BANKER_COMMISSION_BPS = 500
TIE_PAYS_TO_ONE = 8


class BaccaratSide(StrEnum):
    PLAYER = "player"
    BANKER = "banker"
    TIE = "tie"

    def label(self) -> str:
        return {
            BaccaratSide.PLAYER: "Player",
            BaccaratSide.BANKER: "Banker",
            BaccaratSide.TIE: "Tie",
        }[self]


class BaccaratBetResult(StrEnum):
    WIN = "win"
    LOSE = "lose"
    PUSH = "push"


@dataclass(frozen=True, slots=True)
class BaccaratDeal:
    player_cards: tuple[BlackjackCard, ...]
    banker_cards: tuple[BlackjackCard, ...]

    @property
    def player_total(self) -> int:
        return baccarat_total(self.player_cards)

    @property
    def banker_total(self) -> int:
        return baccarat_total(self.banker_cards)

    @property
    def outcome(self) -> BaccaratSide:
        player = self.player_total
        banker = self.banker_total
        if player > banker:
            return BaccaratSide.PLAYER
        if banker > player:
            return BaccaratSide.BANKER
        return BaccaratSide.TIE


def parse_baccarat_side(value: object) -> BaccaratSide:
    raw = str(value or "").strip().lower()
    try:
        return BaccaratSide(raw)
    except ValueError as exc:
        raise GameError("Choose Player, Banker, or Tie.") from exc


def baccarat_card_value(card: BlackjackCard) -> int:
    if card.rank == 0:
        return 1
    if card.rank >= 9:
        return 0
    return card.rank + 1


def baccarat_total(cards: tuple[BlackjackCard, ...] | list[BlackjackCard]) -> int:
    return sum(baccarat_card_value(card) for card in cards) % 10


def is_natural(cards: tuple[BlackjackCard, ...] | list[BlackjackCard]) -> bool:
    return len(cards) == 2 and baccarat_total(cards) >= 8


def banker_draws_third(banker_total: int, player_third: int | None) -> bool:
    if player_third is None:
        return banker_total <= 5
    if banker_total <= 2:
        return True
    if banker_total == 3:
        return player_third != 8
    if banker_total == 4:
        return player_third in {2, 3, 4, 5, 6, 7}
    if banker_total == 5:
        return player_third in {4, 5, 6, 7}
    if banker_total == 6:
        return player_third in {6, 7}
    return False


def deal_baccarat(draw: Callable[[], BlackjackCard]) -> BaccaratDeal:
    return _finish_baccarat(_initial_four(draw), draw)


def _initial_four(draw: Callable[[], BlackjackCard]) -> tuple[list[BlackjackCard], list[BlackjackCard]]:
    player_first = draw()
    banker_first = draw()
    player_second = draw()
    banker_second = draw()
    return [player_first, player_second], [banker_first, banker_second]


def _finish_baccarat(
    hands: tuple[list[BlackjackCard], list[BlackjackCard]],
    draw: Callable[[], BlackjackCard],
) -> BaccaratDeal:
    player, banker = hands
    if is_natural(player) or is_natural(banker):
        return BaccaratDeal(player_cards=tuple(player), banker_cards=tuple(banker))
    player_third: int | None = None
    if baccarat_total(player) <= 5:
        extra = draw()
        player.append(extra)
        player_third = baccarat_card_value(extra)
    if banker_draws_third(baccarat_total(banker), player_third):
        banker.append(draw())
    return BaccaratDeal(player_cards=tuple(player), banker_cards=tuple(banker))


def deal_baccarat_from_cards(cards: list[BlackjackCard]) -> BaccaratDeal:
    index = 0

    def draw() -> BlackjackCard:
        nonlocal index
        if index >= len(cards):
            raise ValueError("Not enough cards to deal baccarat.")
        card = cards[index]
        index += 1
        return card

    player, banker = _initial_four(draw)
    return _finish_baccarat((player, banker), draw)


def profit_lamports(stake_lamports: int, side: BaccaratSide) -> int:
    stake = max(int(stake_lamports), 0)
    if side is BaccaratSide.PLAYER:
        return stake
    if side is BaccaratSide.BANKER:
        return (stake * (10_000 - BANKER_COMMISSION_BPS)) // 10_000
    return stake * TIE_PAYS_TO_ONE


def baccarat_deal_frames(deal: BaccaratDeal) -> list[tuple[tuple[BlackjackCard, ...], tuple[BlackjackCard, ...]]]:
    player: list[BlackjackCard] = []
    banker: list[BlackjackCard] = []
    frames: list[tuple[tuple[BlackjackCard, ...], tuple[BlackjackCard, ...]]] = []

    def snap() -> None:
        frames.append((tuple(player), tuple(banker)))

    if deal.player_cards:
        player.append(deal.player_cards[0])
        snap()
    if deal.banker_cards:
        banker.append(deal.banker_cards[0])
        snap()
    if len(deal.player_cards) > 1:
        player.append(deal.player_cards[1])
        snap()
    if len(deal.banker_cards) > 1:
        banker.append(deal.banker_cards[1])
        snap()
    if len(deal.player_cards) > 2:
        player.append(deal.player_cards[2])
        snap()
    if len(deal.banker_cards) > 2:
        banker.append(deal.banker_cards[2])
        snap()
    return frames


def payout_lamports(stake_lamports: int, side: BaccaratSide, outcome: BaccaratSide) -> tuple[BaccaratBetResult, int]:
    stake = max(int(stake_lamports), 0)
    if outcome is BaccaratSide.TIE:
        if side is BaccaratSide.TIE:
            return BaccaratBetResult.WIN, stake + profit_lamports(stake, side)
        return BaccaratBetResult.PUSH, stake
    if side is BaccaratSide.TIE:
        return BaccaratBetResult.LOSE, 0
    if side is outcome:
        return BaccaratBetResult.WIN, stake + profit_lamports(stake, side)
    return BaccaratBetResult.LOSE, 0
