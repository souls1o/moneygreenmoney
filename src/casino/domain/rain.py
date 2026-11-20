from __future__ import annotations

RAIN_JOIN_STATUS = ".gg/casinounderground #1 Crypto Casino"


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
    activities = list(getattr(member, "activities", None) or ())
    activity = getattr(member, "activity", None)
    if activity is not None:
        activities.append(activity)
    for item in activities:
        for attr in ("name", "state"):
            value = getattr(item, attr, None)
            if not isinstance(value, str):
                continue
            trimmed = value.strip()
            if not trimmed or trimmed in seen:
                continue
            seen.add(trimmed)
            found.append(trimmed)
    return tuple(found)
