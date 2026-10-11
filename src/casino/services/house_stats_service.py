from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from casino.domain.enums import GameStatus
from casino.domain.records import (
    GameRecord,
    HouseStatsReport,
    HouseTreasuryRecord,
    TimeframeStats,
    is_house_seat,
)
from casino.logging.logger import get_logger
from casino.repositories.game_repository import GameRepository
from casino.repositories.house_repository import HouseRepository
from casino.services.price_service import PriceService
from casino.solana_client.rpc import SolanaRpcGateway
from casino.utils.clock import Clock, DateTimeNormalizer


@dataclass(frozen=True, slots=True)
class HouseStatWindow:
    """Named lookback used by house analytics. `delta=None` means all time."""

    label: str
    delta: timedelta | None


HOUSE_STAT_WINDOWS: tuple[HouseStatWindow, ...] = (
    HouseStatWindow("Last 24 hours", timedelta(hours=24)),
    HouseStatWindow("Last 7 days", timedelta(days=7)),
    HouseStatWindow("Last 30 days", timedelta(days=30)),
    HouseStatWindow("All time", None),
)


class HouseStatsCalculator:
    """Pure aggregation of completed games into timeframe house stats."""

    def __init__(self, normalizer: DateTimeNormalizer | None = None) -> None:
        self._normalizer = normalizer or DateTimeNormalizer()

    def report(
        self,
        *,
        games: Sequence[GameRecord],
        open_tables: int,
        treasury: HouseTreasuryRecord,
        sol_price_usd: Decimal,
        now: datetime,
        windows: Sequence[HouseStatWindow] = HOUSE_STAT_WINDOWS,
        super_wallet_lamports: int = 0,
    ) -> HouseStatsReport:
        aware_now = self._require_utc(now)
        return HouseStatsReport(
            house_balance_lamports=treasury.sol_balance_lamports,
            super_wallet_lamports=super_wallet_lamports,
            lifetime_fees_lamports=treasury.total_fees_collected_lamports,
            open_tables=open_tables,
            sol_price_usd=sol_price_usd,
            timeframes=tuple(
                self.for_window(games, window=window, now=aware_now) for window in windows
            ),
        )

    def for_window(
        self,
        games: Sequence[GameRecord],
        *,
        window: HouseStatWindow,
        now: datetime,
    ) -> TimeframeStats:
        since = None if window.delta is None else now - window.delta
        selected = [game for game in games if self._in_window(game, since=since, now=now)]
        unique_players: set[str] = set()
        volume_lamports = 0
        volume_usd = Decimal("0")
        fees_lamports = 0
        fees_usd = Decimal("0")
        call_bot_games = 0
        house_wagered = 0
        house_payout = 0
        for game in selected:
            seats = len(game.player_ids())
            volume_lamports += game.bet_amount_lamports * seats
            volume_usd += game.bet_amount_fiat * seats
            unique_players.update(game.human_player_ids())
            house_seats = [player_id for player_id in game.player_ids() if is_house_seat(player_id)]
            fees_lamports += game.house_fee_lamports
            fees_usd += game.house_fee_usd
            if house_seats:
                call_bot_games += 1
                house_wagered += game.bet_amount_lamports * len(house_seats)
                house_payout += game.house_gross_payout_lamports()
        return TimeframeStats(
            label=window.label,
            games_completed=len(selected),
            unique_players=len(unique_players),
            volume_lamports=volume_lamports,
            volume_usd=volume_usd,
            fees_lamports=fees_lamports,
            fees_usd=fees_usd,
            call_bot_games=call_bot_games,
            house_wagered_lamports=house_wagered,
            house_payout_lamports=house_payout,
            house_net_lamports=fees_lamports + house_payout - house_wagered,
        )

    def _in_window(self, game: GameRecord, *, since: datetime | None, now: datetime) -> bool:
        if game.status is not GameStatus.COMPLETED:
            return False
        resolved = self._normalizer.to_utc(game.resolved_at)
        if resolved is None:
            return False
        if resolved > now:
            return False
        if since is None:
            return True
        return resolved >= since

    def _require_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class HouseStatsService:
    def __init__(
        self,
        game_repository: GameRepository,
        house_repository: HouseRepository,
        price_service: PriceService,
        clock: Clock,
        rpc: SolanaRpcGateway | None = None,
        calculator: HouseStatsCalculator | None = None,
    ) -> None:
        self._games = game_repository
        self._house = house_repository
        self._prices = price_service
        self._clock = clock
        self._rpc = rpc
        self._calculator = calculator or HouseStatsCalculator()
        self._logger = get_logger("casino.house_stats")

    async def snapshot(self) -> HouseStatsReport:
        treasury = await self._house.get()
        games = await self._games.list_completed_since()
        open_games = await self._games.list_open(limit=100)
        quote = await self._prices.get_quote(allow_stale=True)
        return self._calculator.report(
            games=games,
            open_tables=len(open_games),
            treasury=treasury,
            sol_price_usd=quote.sol_price("USD"),
            now=self._clock.now(),
            super_wallet_lamports=await self._super_wallet_balance(),
        )

    async def _super_wallet_balance(self) -> int:
        if self._rpc is None or not self._rpc.super_address:
            return 0
        try:
            return await self._rpc.get_balance(self._rpc.super_address)
        except Exception:
            self._logger.exception("super_wallet_balance_failed")
            return 0
