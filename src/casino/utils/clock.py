from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def to_utc(self, value: datetime | None) -> datetime | None: ...


class DateTimeNormalizer:
    """MongoDB returns naive UTC datetimes; application clocks are timezone-aware."""

    def to_utc(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class SystemClock:
    def __init__(self, normalizer: DateTimeNormalizer | None = None) -> None:
        self._normalizer = normalizer or DateTimeNormalizer()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def to_utc(self, value: datetime | None) -> datetime | None:
        return self._normalizer.to_utc(value)
