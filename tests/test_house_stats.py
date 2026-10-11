from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from casino.domain.enums import CoinSide, GameStatus, GameType
from casino.domain.records import HOUSE_USER_ID, GameRecord, HouseTreasuryRecord
from casino.services.house_stats_service import HouseStatsCalculator, HouseStatsService
from casino.utils.money import SolAmount
from tests.conftest import FrozenClock, StubPriceService
from tests.fakes import MemoryGameRepository, MemoryHouseRepository


def _game(
    *,
    game_id: str,
    resolved_at: datetime,
    bet_lamports: int = 1_000_000_000,
    bet_fiat: str = "10",
    heads: list[str],
    tails: list[str],
    winners: list[str],
    fee_lamports: int = 120_000_000,
    fee_usd: str = "1.20",
    payout_lamports: int = 1_880_000_000,
    status: GameStatus = GameStatus.COMPLETED,
) -> GameRecord:
    return GameRecord(
        game_id=game_id,
        status=status,
        creator_id=heads[0] if heads else tails[0],
        bet_amount_lamports=bet_lamports,
        currency="USD",
        bet_amount_fiat=Decimal(bet_fiat),
        game_type=GameType.COINFLIP,
        heads_ids=list(heads),
        tails_ids=list(tails),
        winner_id=winners[0] if winners else None,
        winner_ids=list(winners),
        winner_side=CoinSide.HEADS if winners and winners[0] in heads else CoinSide.TAILS,
        house_fee_lamports=fee_lamports,
        house_fee_usd=Decimal(fee_usd),
        winner_payout_lamports=payout_lamports,
        per_winner_payout_lamports=payout_lamports,
        coin_result=CoinSide.HEADS if winners and winners[0] in heads else CoinSide.TAILS,
        resolved_at=resolved_at,
        created_at=resolved_at,
    )


class TestHouseStatsCalculator:
    def test_splits_games_by_timeframe_and_includes_call_bot_net(self) -> None:
        now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
        recent = _game(
            game_id="recent",
            resolved_at=now - timedelta(hours=2),
            heads=["p1"],
            tails=["p2"],
            winners=["p1"],
        )
        week = _game(
            game_id="week",
            resolved_at=now - timedelta(days=3),
            heads=["p3"],
            tails=[HOUSE_USER_ID],
            winners=[HOUSE_USER_ID],
            fee_lamports=0,
            fee_usd="0",
        )
        old = _game(
            game_id="old",
            resolved_at=now - timedelta(days=40),
            heads=["p4"],
            tails=["p5"],
            winners=["p5"],
        )
        pending = _game(
            game_id="open",
            resolved_at=now,
            heads=["p6"],
            tails=[],
            winners=[],
            status=GameStatus.PENDING,
        )
        report = HouseStatsCalculator().report(
            games=[recent, week, old, pending],
            open_tables=2,
            treasury=HouseTreasuryRecord(sol_balance_lamports=5_000_000_000, total_fees_collected_lamports=360_000_000),
            sol_price_usd=Decimal("100"),
            now=now,
        )
        assert report.house_balance_lamports == 5_000_000_000
        assert report.super_wallet_lamports == 0
        assert report.open_tables == 2
        by_label = {window.label: window for window in report.timeframes}

        day = by_label["Last 24 hours"]
        assert day.games_completed == 1
        assert day.unique_players == 2
        assert day.volume_lamports == 2_000_000_000
        assert day.volume_usd == Decimal("20")
        assert day.fees_lamports == 120_000_000
        assert day.call_bot_games == 0
        assert day.house_net_lamports == 120_000_000

        seven = by_label["Last 7 days"]
        assert seven.games_completed == 2
        assert seven.call_bot_games == 1
        assert seven.fees_lamports == 120_000_000
        assert seven.house_wagered_lamports == 1_000_000_000
        assert seven.house_payout_lamports == 2_000_000_000
        assert seven.house_net_lamports == 120_000_000 + 2_000_000_000 - 1_000_000_000

        month = by_label["Last 30 days"]
        assert month.games_completed == 2

        all_time = by_label["All time"]
        assert all_time.games_completed == 3
        assert all_time.unique_players == 5
        assert all_time.fees_lamports == 240_000_000


@pytest.mark.asyncio
async def test_house_stats_service_reads_treasury_and_games() -> None:
    clock = FrozenClock(datetime(2026, 9, 7, 12, 0, tzinfo=UTC))
    games = MemoryGameRepository()
    house = MemoryHouseRepository()
    house.record.sol_balance_lamports = SolAmount.parse("3").lamports
    house.record.total_fees_collected_lamports = SolAmount.parse("0.12").lamports
    await games.insert(
        _game(
            game_id="done",
            resolved_at=clock.now(),
            heads=["p1"],
            tails=["p2"],
            winners=["p1"],
        )
    )
    await games.insert(
        _game(
            game_id="waiting",
            resolved_at=clock.now(),
            heads=["p3"],
            tails=[],
            winners=[],
            status=GameStatus.PENDING,
        )
    )
    class StubRpc:
        super_address = "Super11111111111111111111111111111111112"

        async def get_balance(self, address: str) -> int:
            assert address == self.super_address
            return SolAmount.parse("7.5").lamports

    service = HouseStatsService(games, house, StubPriceService(Decimal("100")), clock, StubRpc())  # type: ignore[arg-type]
    report = await service.snapshot()
    assert report.house_balance_lamports == SolAmount.parse("3").lamports
    assert report.super_wallet_lamports == SolAmount.parse("7.5").lamports
    assert report.open_tables == 1
    all_time = report.timeframes[-1]
    assert all_time.label == "All time"
    assert all_time.games_completed == 1
    assert all_time.volume_usd == Decimal("20")


def test_house_winner_share_is_the_full_pot_half() -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    game = _game(
        game_id="house-win",
        resolved_at=now,
        bet_fiat="100",
        heads=[HOUSE_USER_ID],
        tails=["p1"],
        winners=[HOUSE_USER_ID],
        fee_lamports=0,
        fee_usd="0",
        payout_lamports=1_880_000_000,
    )
    assert game.winner_gross_share_fiat() == Decimal("200")
    assert game.house_gross_payout_lamports() == 2_000_000_000
    assert game.house_game_pnl_fiat() == Decimal("100")
    assert game.house_net_fiat() == Decimal("100")


def test_house_loss_is_negative_game_pnl_plus_fees() -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    game = _game(
        game_id="house-loss",
        resolved_at=now,
        bet_fiat="100",
        heads=["p1"],
        tails=[HOUSE_USER_ID],
        winners=["p1"],
        fee_usd="12",
    )
    assert game.house_game_pnl_fiat() == Decimal("-100")
    assert game.house_net_fiat() == Decimal("-88")
