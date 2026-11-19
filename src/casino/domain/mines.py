from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from fractions import Fraction

from casino.errors import GameError

MINES_EDGE = Decimal("0.03")
MINES_TILES = 25
MINES_MIN = 1
MINES_MAX = 24
MINES_TICK = Decimal("0.01")


def parse_mines_count(value: object) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise GameError("Choose between 1 and 24 mines.") from exc
    if parsed < MINES_MIN or parsed > MINES_MAX:
        raise GameError("Choose between 1 and 24 mines.")
    return parsed


def mines_gems_available(mines: int, *, tiles: int = MINES_TILES) -> int:
    count = parse_mines_count(mines)
    return tiles - count


def mines_survival_chance(mines: int, gems: int, *, tiles: int = MINES_TILES) -> Fraction:
    count = parse_mines_count(mines)
    if gems < 0 or gems > tiles - count:
        raise GameError("That many gems cannot be revealed on this board.")
    chance = Fraction(1)
    for step in range(gems):
        chance *= Fraction(tiles - count - step, tiles - step)
    return chance


def mines_multiplier(mines: int, gems: int, *, tiles: int = MINES_TILES) -> Decimal:
    if gems == 0:
        return Decimal("1.00")
    chance = mines_survival_chance(mines, gems, tiles=tiles)
    raw = (Decimal("1") - MINES_EDGE) * Decimal(chance.denominator) / Decimal(chance.numerator)
    return raw.quantize(MINES_TICK, rounding=ROUND_DOWN)


def mines_payout_lamports(bet_lamports: int, mines: int, gems: int, *, tiles: int = MINES_TILES) -> int:
    if gems <= 0:
        return 0
    chance = mines_survival_chance(mines, gems, tiles=tiles)
    raw = (
        Decimal(int(bet_lamports))
        * (Decimal("1") - MINES_EDGE)
        * Decimal(chance.denominator)
        / Decimal(chance.numerator)
    )
    return int(raw.to_integral_value(rounding=ROUND_DOWN))


def mines_max_payout_lamports(bet_lamports: int, mines: int, *, tiles: int = MINES_TILES) -> int:
    return mines_payout_lamports(bet_lamports, mines, mines_gems_available(mines, tiles=tiles), tiles=tiles)


def mines_max_profit_lamports(bet_lamports: int, mines: int, *, tiles: int = MINES_TILES) -> int:
    return max(mines_max_payout_lamports(bet_lamports, mines, tiles=tiles) - int(bet_lamports), 0)


def format_mines_multiplier(value: Decimal) -> str:
    quantized = Decimal(value).quantize(MINES_TICK, rounding=ROUND_DOWN)
    return format(quantized, "f")
