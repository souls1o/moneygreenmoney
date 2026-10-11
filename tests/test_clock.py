from datetime import UTC, datetime

from casino.utils.clock import DateTimeNormalizer


class TestDateTimeNormalizer:
    def test_naive_assumed_utc(self) -> None:
        naive = datetime(2026, 9, 5, 22, 0, 0)
        aware = DateTimeNormalizer().to_utc(naive)
        assert aware is not None
        assert aware.tzinfo is UTC
        assert aware.replace(tzinfo=None) == naive

    def test_aware_converted_to_utc(self) -> None:
        aware = datetime(2026, 9, 5, 22, 0, 0, tzinfo=UTC)
        assert DateTimeNormalizer().to_utc(aware) == aware
