from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from casino.domain.enums import GameStatus, JackpotStatus
from casino.domain.investor import equity_lamports
from casino.domain.records import (
    GameRecord,
    HouseStatsReport,
    HouseTreasuryRecord,
    JackpotRecord,
    TimeframeStats,
    is_house_seat,
)
from casino.logging.logger import get_logger
from casino.repositories.game_repository import GameRepository
from casino.repositories.house_repository import HouseRepository
from casino.repositories.jackpot_repository import JackpotRepository
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
        jackpots: Sequence[JackpotRecord] = (),
    ) -> HouseStatsReport:
        aware_now = self._require_utc(now)
        pool = treasury.pool_shares()
        investor_pool = equity_lamports(treasury.investor_shares, treasury.sol_balance_lamports, pool)
        founder_pool = (
            equity_lamports(treasury.founder_shares, treasury.sol_balance_lamports, pool)
            if pool
            else treasury.sol_balance_lamports
        )
        return HouseStatsReport(
            house_balance_lamports=treasury.sol_balance_lamports,
            super_wallet_lamports=super_wallet_lamports,
            lifetime_fees_lamports=treasury.total_fees_collected_lamports,
            open_tables=open_tables,
            sol_price_usd=sol_price_usd,
            timeframes=tuple(
                self.for_window(games, jackpots, window=window, now=aware_now)
                for window in windows
            ),
            founder_shares=treasury.founder_shares,
            investor_shares=treasury.investor_shares,
            founder_pool_lamports=founder_pool,
            investor_pool_lamports=investor_pool,
        )

    def for_window(
        self,
        games: Sequence[GameRecord],
        jackpots: Sequence[JackpotRecord] = (),
        *,
        window: HouseStatWindow,
        now: datetime,
    ) -> TimeframeStats:
        since = None if window.delta is None else now - window.delta
        selected = [game for game in games if self._in_window(game, since=since, now=now)]
        selected_jackpots = [
            jackpot for jackpot in jackpots if self._jackpot_in_window(jackpot, since=since, now=now)
        ]
        unique_players: set[str] = set()
        volume_lamports = 0
        volume_usd = Decimal("0")
        fees_lamports = 0
        fees_usd = Decimal("0")
        call_bot_games = 0
        house_wagered = 0
        house_payout = 0
        house_net = 0
        for game in selected:
            human_volume = game.human_volume_lamports()
            volume_lamports += human_volume
            if game.bet_amount_lamports:
                volume_usd += game.stake_usd() * Decimal(human_volume) / Decimal(game.bet_amount_lamports)
            unique_players.update(game.human_player_ids())
            fees_lamports += game.house_fee_lamports
            fees_usd += game.house_fee_usd
            pnl = game.house_game_pnl_lamports()
            house_net += game.house_fee_lamports + pnl
            if game.is_vs_house() or not any(is_house_seat(player_id) for player_id in game.player_ids()):
                continue
            call_bot_games += 1
            house_seats = [player_id for player_id in game.player_ids() if is_house_seat(player_id)]
            house_wagered += sum(game.wager_for(player_id).lamports for player_id in house_seats)
            house_payout += game.house_gross_payout_lamports()
        for jackpot in selected_jackpots:
            volume_lamports += jackpot.pot_lamports()
            unique_players.update(jackpot.player_ids())
            fees_lamports += int(jackpot.house_fee_lamports)
            house_net += int(jackpot.house_fee_lamports)
        return TimeframeStats(
            label=window.label,
            games_completed=len(selected) + len(selected_jackpots),
            unique_players=len(unique_players),
            volume_lamports=volume_lamports,
            volume_usd=volume_usd,
            fees_lamports=fees_lamports,
            fees_usd=fees_usd,
            call_bot_games=call_bot_games,
            house_wagered_lamports=house_wagered,
            house_payout_lamports=house_payout,
            house_net_lamports=house_net,
        )

    def _in_window(self, game: GameRecord, *, since: datetime | None, now: datetime) -> bool:
        if game.status is not GameStatus.COMPLETED:
            return False
        return self._resolved_in_window(game.resolved_at, since=since, now=now)

    def _jackpot_in_window(
        self, jackpot: JackpotRecord, *, since: datetime | None, now: datetime
    ) -> bool:
        if jackpot.status is not JackpotStatus.COMPLETED:
            return False
        return self._resolved_in_window(jackpot.resolved_at, since=since, now=now)

    def _resolved_in_window(
        self, resolved_at: datetime | None, *, since: datetime | None, now: datetime
    ) -> bool:
        resolved = self._normalizer.to_utc(resolved_at)
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
        jackpot_repository: JackpotRepository | None = None,
    ) -> None:
        self._games = game_repository
        self._house = house_repository
        self._prices = price_service
        self._clock = clock
        self._rpc = rpc
        self._calculator = calculator or HouseStatsCalculator()
        self._jackpots = jackpot_repository
        self._logger = get_logger("casino.house_stats")

    async def snapshot(self) -> HouseStatsReport:
        treasury = await self._house.get()
        games = await self._games.list_completed_since(treasury.stats_reset_at)
        jackpots: list[JackpotRecord] = []
        if self._jackpots is not None:
            jackpots = await self._jackpots.list_completed_since(treasury.stats_reset_at)
        quote = await self._prices.get_quote(allow_stale=True)
        return self._calculator.report(
            games=games,
            open_tables=await self._games.count_open(),
            treasury=treasury,
            sol_price_usd=quote.sol_price("USD"),
            now=self._clock.now(),
            super_wallet_lamports=await self._super_wallet_balance(),
            jackpots=jackpots,
        )

    async def _super_wallet_balance(self) -> int:
        if self._rpc is None or not self._rpc.super_address:
            return 0
        try:
            return await self._rpc.get_balance(self._rpc.super_address)
        except Exception:
            self._logger.exception("super_wallet_balance_failed")
            return 0
