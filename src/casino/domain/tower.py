from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from fractions import Fraction

from casino.errors import GameError

TOWER_EDGE = Decimal("0.03")
TOWER_FLOORS = 8
TOWER_TICK = Decimal("0.01")
TOWER_DIFFICULTIES = {
    "easy": 4,
    "medium": 3,
    "hard": 2,
}
TOWER_MINES_PER_FLOOR = 1


def parse_tower_difficulty(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw not in TOWER_DIFFICULTIES:
        raise GameError("Choose Easy, Medium, or Hard.")
    return raw


def tower_tiles_for(difficulty: object) -> int:
    return TOWER_DIFFICULTIES[parse_tower_difficulty(difficulty)]


def tower_survival_chance(tiles: int, floors: int) -> Fraction:
    columns = int(tiles)
    climbed = int(floors)
    if columns < 2 or columns > 4:
        raise GameError("Tower difficulty is invalid.")
    if climbed < 0 or climbed > TOWER_FLOORS:
        raise GameError("That many floors cannot be climbed.")
    safe = columns - TOWER_MINES_PER_FLOOR
    chance = Fraction(1)
    for _ in range(climbed):
        chance *= Fraction(safe, columns)
    return chance


def tower_multiplier(tiles: int, floors: int) -> Decimal:
    if floors <= 0:
        return Decimal("1.00")
    chance = tower_survival_chance(tiles, floors)
    raw = (Decimal("1") - TOWER_EDGE) * Decimal(chance.denominator) / Decimal(chance.numerator)
    return raw.quantize(TOWER_TICK, rounding=ROUND_DOWN)


def tower_payout_lamports(bet_lamports: int, tiles: int, floors: int) -> int:
    if floors <= 0:
        return 0
    chance = tower_survival_chance(tiles, floors)
    raw = (
        Decimal(int(bet_lamports))
        * (Decimal("1") - TOWER_EDGE)
        * Decimal(chance.denominator)
        / Decimal(chance.numerator)
    )
    return int(raw.to_integral_value(rounding=ROUND_DOWN))


def format_tower_multiplier(value: Decimal) -> str:
    quantized = Decimal(value).quantize(TOWER_TICK, rounding=ROUND_DOWN)
    return format(quantized, "f")
