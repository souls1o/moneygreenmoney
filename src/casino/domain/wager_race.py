from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from casino.domain.enums import GameType
from casino.domain.limbo import LIMBO_EDGE, parse_limbo_multiplier
from casino.errors import ValidationError
from casino.utils.money import SolAmount

RACE_WEEKDAY = 0
RACE_HOUR_GMT = 10
RACE_PLACES = 5
_PRIZE_TICK = Decimal("0.01")


def race_week_start(now: datetime) -> datetime:
    instant = now.astimezone(UTC)
    monday = datetime(instant.year, instant.month, instant.day, RACE_HOUR_GMT, 0, tzinfo=UTC) - timedelta(
        days=instant.weekday()
    )
    if instant < monday:
        monday -= timedelta(days=7)
    return monday


def race_week_end(starts_at: datetime) -> datetime:
    return starts_at.astimezone(UTC) + timedelta(days=7)


def race_id_for(guild_id: str, starts_at: datetime) -> str:
    stamp = starts_at.astimezone(UTC).strftime("%Y%m%d")
    return f"wr_{guild_id}_{stamp}"


def parse_prize_usd(raw: str | Decimal | None) -> Decimal:
    if raw is None or str(raw).strip() == "":
        return Decimal("0")
    try:
        value = Decimal(str(raw).strip())
    except Exception as exc:
        raise ValidationError("Enter a valid USD prize.") from exc
    if not value.is_finite() or value < 0:
        raise ValidationError("Prize amounts cannot be negative.")
    return value.quantize(_PRIZE_TICK, rounding=ROUND_DOWN)


def prize_usd_list(first: Decimal, second: Decimal, third: Decimal, fourth_fifth: Decimal) -> list[Decimal]:
    return [first, second, third, fourth_fifth, fourth_fifth]


def limbo_qualifying_lamports(wager_lamports: int, target: Decimal | None) -> int:
    """Count the amount at risk so 1.01x spam cannot farm the race."""
    if wager_lamports <= 0 or target is None:
        return 0
    win_chance = (Decimal("1") - LIMBO_EDGE) / parse_limbo_multiplier(target)
    lose_chance = Decimal("1") - win_chance
    if lose_chance <= 0:
        return 0
    return int((Decimal(wager_lamports) * lose_chance).to_integral_value(rounding=ROUND_DOWN))


def qualifying_lamports(
    wager_lamports: int,
    *,
    game_type: GameType,
    limbo_target: Decimal | None = None,
) -> int:
    if wager_lamports <= 0:
        return 0
    if game_type is GameType.LIMBO:
        return limbo_qualifying_lamports(wager_lamports, limbo_target)
    return int(wager_lamports)


def ranked_entries(wagers: dict[str, int], *, limit: int = RACE_PLACES) -> list[tuple[str, int]]:
    ordered = sorted(
        ((user_id, int(lamports)) for user_id, lamports in wagers.items() if int(lamports) > 0),
        key=lambda item: (-item[1], item[0]),
    )
    return ordered[:limit]


def prizes_to_lamports(prizes_usd: list[Decimal], sol_price_usd: Decimal) -> list[int]:
    if sol_price_usd <= 0:
        return [0 for _ in prizes_usd]
    out: list[int] = []
    for usd in prizes_usd:
        if usd <= 0:
            out.append(0)
            continue
        sol = (usd / sol_price_usd).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
        out.append(SolAmount.from_decimal(sol).lamports)
    return out
