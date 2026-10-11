from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from casino.domain.records import UserRecord
from casino.services.balance_service import BalanceService
from casino.services.game_service import GameService
from casino.services.ledger_service import LedgerService
from casino.services.price_service import PriceQuote
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.clock import DateTimeNormalizer, SystemClock
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService, SolAmount
from casino.utils.rng import FairCommitment
from tests.fakes import (
    MemoryGameRepository,
    MemoryHouseRepository,
    MemoryLedgerRepository,
    MemoryTransactionRepository,
    MemoryUserRepository,
    NullUnitOfWorkFactory,
)


class FrozenClock:
    def __init__(self, moment: datetime | None = None) -> None:
        self._moment = moment or datetime(2026, 9, 4, 8, 0, 0, tzinfo=UTC)
        self._normalizer = DateTimeNormalizer()

    def now(self) -> datetime:
        return self._moment

    def advance(self, seconds: int) -> None:
        self._moment = self._moment + timedelta(seconds=seconds)

    def to_utc(self, value: datetime | None) -> datetime | None:
        return self._normalizer.to_utc(value)


class StubPriceService:
    def __init__(self, usd: Decimal = Decimal("100"), extra: dict[str, Decimal] | None = None) -> None:
        prices = {"usd": usd, "eur": Decimal("90")}
        if extra:
            prices.update(extra)
        self._quote = PriceQuote(prices=prices, fetched_at=FrozenClock().now(), stale=False)

    async def get_quote(self, *, allow_stale: bool = True) -> PriceQuote:
        return self._quote

    def set_usd(self, usd: Decimal) -> None:
        prices = dict(self._quote.prices)
        prices["usd"] = usd
        self._quote = PriceQuote(prices=prices, fetched_at=self._quote.fetched_at, stale=self._quote.stale)


class StubSettings:
    game_ttl_seconds = 300
    auto_flip_after_seconds = 3600
    table_idle_delete_seconds = 300
    house_fee_bps = 300
    min_bet_usd = "0.01"
    max_call_bot_usd = "1000"
    min_rain_usd = "50"

    @property
    def house_fee_rate_display(self) -> str:
        return "3%"
    min_rain_seconds = 10
    max_rain_seconds = 604_800
    default_currency = "USD"
    discord_guild_id = 99

    def verify_page_url(self) -> str:
        return "https://example.com/verify"


class FixedRandomService:
    def __init__(
        self,
        side,
        dice_pairs: list[tuple[int, int]] | None = None,
        coin_sides: list | None = None,
    ) -> None:
        self._side = side
        self._dice_pairs = dice_pairs
        self._coin_sides = coin_sides

    def commit(self) -> FairCommitment:
        return FairCommitment(server_seed="fixed-server-seed", server_seed_hash="fixed-hash")

    def hash_server_seed(self, server_seed: str) -> str:
        return "fixed-hash" if server_seed == "fixed-server-seed" else "mismatch"

    def resolve_coin(self, server_seed: str, client_seed: str, nonce: int, flip_index: int = 0):
        if self._coin_sides:
            return self._coin_sides[flip_index % len(self._coin_sides)]
        return self._side

    def resolve_dice(self, server_seed: str, client_seed: str, nonce: int, roll_index: int):
        from casino.utils.rng import FairDice

        pairs = self._dice_pairs or [(6, 6)]
        die_a, die_b = pairs[roll_index % len(pairs)]
        return FairDice(digest="fixed-dice", die_a=die_a, die_b=die_b, roll_index=roll_index)

    def client_seed(self) -> str:
        return "fixed-client-seed"


@pytest.fixture
def math_service() -> MathService:
    return MathService(300)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()
