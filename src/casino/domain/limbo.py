from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from casino.errors import GameError

LIMBO_EDGE = Decimal("0.03")
LIMBO_MIN_MULTIPLIER = Decimal("1.01")
LIMBO_MAX_MULTIPLIER = Decimal("1000000")
LIMBO_TICK = Decimal("0.01")
LIMBO_HASH_BITS = 52
LIMBO_HASH_RANGE = Decimal(1 << LIMBO_HASH_BITS)


def parse_limbo_multiplier(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value).strip().replace("x", "").replace("X", ""))
    except Exception as exc:
        raise GameError("Choose a multiplier from 1.01 to 1,000,000.") from exc
    if not parsed.is_finite():
        raise GameError("Choose a multiplier from 1.01 to 1,000,000.")
    target = parsed.quantize(LIMBO_TICK, rounding=ROUND_DOWN)
    if target < LIMBO_MIN_MULTIPLIER or target > LIMBO_MAX_MULTIPLIER:
        raise GameError("Choose a multiplier from 1.01 to 1,000,000.")
    return target


def limbo_payout_lamports(bet_lamports: int, multiplier: Decimal) -> int:
    target = parse_limbo_multiplier(multiplier)
    return int((Decimal(bet_lamports) * target).to_integral_value(rounding=ROUND_DOWN))


def limbo_profit_lamports(bet_lamports: int, multiplier: Decimal) -> int:
    payout = limbo_payout_lamports(bet_lamports, multiplier)
    return max(payout - int(bet_lamports), 0)


def limbo_win_chance(multiplier: Decimal) -> Decimal:
    target = parse_limbo_multiplier(multiplier)
    return ((Decimal("1") - LIMBO_EDGE) / target * Decimal("100")).quantize(
        Decimal("0.0001"), rounding=ROUND_DOWN
    )


def crash_from_digest(digest: str) -> Decimal:
    raw = int(digest[:13], 16)
    sample = (Decimal(raw) + Decimal("1")) / LIMBO_HASH_RANGE
    crash = ((Decimal("1") - LIMBO_EDGE) / sample).quantize(LIMBO_TICK, rounding=ROUND_DOWN)
    if crash < Decimal("1.00"):
        crash = Decimal("1.00")
    if crash > LIMBO_MAX_MULTIPLIER:
        return LIMBO_MAX_MULTIPLIER
    return crash


def format_limbo_multiplier(value: Decimal) -> str:
    quantized = Decimal(value).quantize(LIMBO_TICK, rounding=ROUND_DOWN)
    return format(quantized, "f")


LIMBO_START = Decimal("1.00")
LIMBO_CRASH_SECONDS = 5.0
LIMBO_CRASH_STEP = 0.1
LIMBO_CRASH_FRAMES = 50


def limbo_crash_at(progress: float, crash: Decimal) -> Decimal:
    goal = Decimal(crash).quantize(LIMBO_TICK, rounding=ROUND_DOWN)
    t = min(1.0, max(0.0, float(progress)))
    if goal <= LIMBO_START or t <= 0:
        return LIMBO_START
    if t >= 1:
        return goal
    raw = float(LIMBO_START) * (float(goal) / float(LIMBO_START)) ** t
    value = Decimal(str(raw)).quantize(LIMBO_TICK, rounding=ROUND_DOWN)
    if value < LIMBO_START:
        return LIMBO_START
    if value > goal:
        return goal
    return value


def limbo_crash_frames(crash: Decimal, *, max_frames: int = LIMBO_CRASH_FRAMES) -> list[Decimal]:
    """Exponential climb from 1.00x to the crash across a fixed 5s clock."""
    goal = Decimal(crash).quantize(LIMBO_TICK, rounding=ROUND_DOWN)
    if goal <= LIMBO_START:
        return [LIMBO_START]
    count = max(2, max_frames)
    frames: list[Decimal] = []
    for index in range(count):
        value = limbo_crash_at(index / (count - 1), goal)
        if not frames or value > frames[-1]:
            frames.append(value)
    if frames[-1] != goal:
        frames.append(goal)
    return frames
