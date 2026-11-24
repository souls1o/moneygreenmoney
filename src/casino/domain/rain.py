from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from casino.errors import ValidationError
from casino.utils.money import FIAT_QUANTUM, RAIN_JOIN_DEPOSIT_BPS, RAIN_JOIN_WAGER_BPS

RAIN_JOIN_STATUS = ".gg/casinounderground #1 Crypto Casino"
_STATUS_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


@dataclass(frozen=True, slots=True)
class RainJoinEligibility:
    currency: str
    wager_required_fiat: Decimal
    deposit_required_fiat: Decimal
    wagered_fiat: Decimal
    deposited_fiat: Decimal
    wager_ok: bool
    deposit_ok: bool
    status_ok: bool
    is_creator: bool
    already_joined: bool
    is_open: bool
    has_ended: bool

    @property
    def required_fiat(self) -> Decimal:
        return self.wager_required_fiat

    def requirements_met(self) -> bool:
        return self.wager_ok and self.deposit_ok and self.status_ok


def normalize_custom_status(text: str) -> str:
    cleaned = text.translate(_STATUS_INVISIBLE)
    return " ".join(cleaned.split())


def matches_rain_join_status(*texts: str | None) -> bool:
    required = normalize_custom_status(RAIN_JOIN_STATUS)
    return any(
        text is not None and normalize_custom_status(str(text)) == required for text in texts
    )


def rain_status_texts(member: object) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    try:
        activities = list(getattr(member, "activities", None) or ())
        activity = getattr(member, "activity", None)
        if activity is not None:
            activities.append(activity)
        for item in activities:
            if item is None:
                continue
            values: list[object] = [getattr(item, attr, None) for attr in ("name", "state", "details")]
            for value in values:
                if not isinstance(value, str):
                    continue
                trimmed = value.strip()
                if not trimmed or trimmed == "Custom Status" or trimmed in seen:
                    continue
                seen.add(trimmed)
                found.append(trimmed)
    except Exception:
        return tuple(found)
    return tuple(found)


def parse_rain_requirement_usd(raw: str | Decimal) -> Decimal:
    try:
        value = Decimal(str(raw).strip())
    except Exception as exc:
        raise ValidationError("Enter a valid USD amount.") from exc
    if not value.is_finite() or value < 0:
        raise ValidationError("Requirement amounts cannot be negative.")
    return value.quantize(FIAT_QUANTUM, rounding=ROUND_DOWN)


def default_rain_requirement_lamports(amount_lamports: int, *, deposit: bool = False) -> int:
    bps = RAIN_JOIN_DEPOSIT_BPS if deposit else RAIN_JOIN_WAGER_BPS
    return (max(int(amount_lamports), 0) * bps) // 10_000


def default_rain_requirement_fiat(amount_fiat: Decimal, *, deposit: bool = False) -> Decimal:
    bps = RAIN_JOIN_DEPOSIT_BPS if deposit else RAIN_JOIN_WAGER_BPS
    return (amount_fiat * Decimal(bps) / Decimal(10_000)).quantize(FIAT_QUANTUM, rounding=ROUND_DOWN)
