from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

RAIN_JOIN_STATUS = ".gg/casinounderground #1 Crypto Casino"


@dataclass(frozen=True, slots=True)
class RainJoinEligibility:
    currency: str
    required_fiat: Decimal
    wagered_fiat: Decimal
    deposited_fiat: Decimal
    wager_ok: bool
    deposit_ok: bool
    status_ok: bool
    is_creator: bool
    already_joined: bool
    is_open: bool
    has_ended: bool

    def requirements_met(self) -> bool:
        return self.wager_ok and self.deposit_ok and self.status_ok


def normalize_custom_status(text: str) -> str:
    return " ".join(text.split())


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
            for attr in ("name", "state"):
                value = getattr(item, attr, None)
                if not isinstance(value, str):
                    continue
                trimmed = value.strip()
                if not trimmed or trimmed in seen:
                    continue
                seen.add(trimmed)
                found.append(trimmed)
    except Exception:
        return tuple(found)
    return tuple(found)
