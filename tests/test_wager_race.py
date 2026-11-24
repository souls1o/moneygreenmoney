from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import pytest

from casino.domain.enums import GameType, LedgerEntryType, WagerRaceStatus
from casino.domain.investor import equity_lamports
from casino.domain.limbo import LIMBO_EDGE
from casino.domain.records import GuildConfigRecord, UserRecord
from casino.domain.wager_race import (
    limbo_qualifying_lamports,
    qualifying_lamports,
    race_week_end,
    race_week_start,
    ranked_entries,
)
from casino.services.balance_service import BalanceService
from casino.services.ledger_service import LedgerService
from casino.services.wager_race_service import WagerRaceService
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import SolAmount
from tests.conftest import FrozenClock, StubPriceService
from tests.fakes import (
    MemoryHouseRepository,
    MemoryLedgerRepository,
    MemoryTransactionRepository,
    MemoryUserRepository,
    MemoryWagerRaceRepository,
    NullUnitOfWorkFactory,
)


def test_week_starts_monday_10_gmt() -> None:
    monday_morning = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
    assert race_week_start(monday_morning) == datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
    monday_open = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
    assert race_week_start(monday_open) == datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
    sunday = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)
    start = race_week_start(sunday)
    assert start == datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
    assert race_week_end(start) == datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


def test_limbo_low_multiplier_counts_almost_none() -> None:
    wager = 1_000_000_000
    low = limbo_qualifying_lamports(wager, Decimal("1.01"))
    mid = limbo_qualifying_lamports(wager, Decimal("2.00"))
    assert low < wager // 20
    assert mid > wager // 3
    assert qualifying_lamports(wager, game_type=GameType.COINFLIP) == wager
    assert qualifying_lamports(wager, game_type=GameType.LIMBO, limbo_target=Decimal("1.01")) == low
    expected_mid = int((Decimal(wager) * (Decimal("1") - (Decimal("1") - LIMBO_EDGE) / Decimal("2"))).to_integral_value())
    assert mid == expected_mid


def test_ranked_entries_sort_by_wager() -> None:
    ranked = ranked_entries({"a": 10, "b": 50, "c": 50, "d": 1}, limit=3)
    assert ranked[0][1] == 50
    assert {ranked[0][0], ranked[1][0]} == {"b", "c"}
    assert ranked[2] == ("a", 10)


class _Configs:
    def __init__(self, config: GuildConfigRecord) -> None:
        self.config = config

    async def get(self, guild_id: str) -> GuildConfigRecord:
        return self.config

    async def list_wager_race_guild_ids(self) -> list[str]:
        return [self.config.guild_id] if self.config.wager_race_channel_id else []


def _service(*, now: datetime | None = None):
    clock = FrozenClock(now or datetime(2026, 11, 23, 12, 0, tzinfo=UTC))
    ids = IdentifierFactory()
    users = MemoryUserRepository()
    ledger_repo = MemoryLedgerRepository()
    tx_repo = MemoryTransactionRepository()
    house = MemoryHouseRepository()
    house.record.sol_balance_lamports = 10_000_000_000
    house.record.founder_shares = 6_000_000_000
    house.record.investor_shares = 4_000_000_000
    races = MemoryWagerRaceRepository()
    config = GuildConfigRecord(
        guild_id="99",
        wager_race_channel_id="chan",
        wager_race_first_usd=Decimal("100"),
        wager_race_second_usd=Decimal("50"),
        wager_race_third_usd=Decimal("25"),
        wager_race_fourth_fifth_usd=Decimal("10"),
    )
    balances = BalanceService(users, tx_repo, house, LedgerService(ledger_repo, ids, clock), ids, clock)
    service = WagerRaceService(
        races,
        _Configs(config),
        balances,
        house,
        StubPriceService(),
        clock,
        NullUnitOfWorkFactory(),
    )
    return service, users, house, races, ledger_repo, clock


@pytest.mark.asyncio
async def test_record_wager_ranks_players() -> None:
    service, _users, _house, races, _ledger, _clock = _service()
    race = await service.ensure_open("99")
    await service.record_wager("99", "1", 1_000_000_000, game_type=GameType.COINFLIP)
    await service.record_wager("99", "2", 5_000_000_000, game_type=GameType.COINFLIP)
    await service.record_wager("99", "3", 2_000_000_000, game_type=GameType.LIMBO, limbo_target=Decimal("1.01"))
    live = await races.find_open("99")
    assert live is not None
    assert live.race_id == race.race_id
    ranked = live.ranked()
    assert ranked[0] == ("2", 5_000_000_000)
    assert ranked[1] == ("1", 1_000_000_000)
    assert ranked[2][0] == "3"
    assert ranked[2][1] < 200_000_000


@pytest.mark.asyncio
async def test_settle_pays_from_house_book_by_ownership() -> None:
    service, users, house, _races, ledger, clock = _service()
    book_before = house.record.sol_balance_lamports
    founder_before = equity_lamports(
        house.record.founder_shares, book_before, house.record.pool_shares()
    )
    investor_before = equity_lamports(
        house.record.investor_shares, book_before, house.record.pool_shares()
    )
    for user_id in ("1", "2", "3", "4", "5"):
        users.users[user_id] = UserRecord(discord_id=user_id, created_at=clock.now(), updated_at=clock.now())
    await service.ensure_open("99")
    await service.record_wager("99", "1", 5_000_000_000, game_type=GameType.DICE)
    await service.record_wager("99", "2", 4_000_000_000, game_type=GameType.DICE)
    await service.record_wager("99", "3", 3_000_000_000, game_type=GameType.DICE)
    await service.record_wager("99", "4", 2_000_000_000, game_type=GameType.DICE)
    await service.record_wager("99", "5", 1_000_000_000, game_type=GameType.DICE)
    clock.advance(int(timedelta(days=7).total_seconds()))
    settled = await service.settle_due()
    assert len(settled) == 1
    race = settled[0]
    assert race.status is WagerRaceStatus.COMPLETED
    assert race.place_ids[:5] == ["1", "2", "3", "4", "5"]
    total_paid = sum(race.payouts_lamports.values())
    assert total_paid > 0
    assert house.record.sol_balance_lamports == book_before - total_paid
    founder_after = equity_lamports(
        house.record.founder_shares, house.record.sol_balance_lamports, house.record.pool_shares()
    )
    investor_after = equity_lamports(
        house.record.investor_shares, house.record.sol_balance_lamports, house.record.pool_shares()
    )
    assert founder_after < founder_before
    assert investor_after < investor_before
    assert users.users["1"].sol_balance_lamports == race.payouts_lamports["1"]
    prizes = [entry for entry in ledger.entries if entry.type is LedgerEntryType.WAGER_RACE]
    assert prizes
