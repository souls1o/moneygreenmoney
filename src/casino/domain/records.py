from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from casino.domain.enums import (
    CoinSide,
    GameMode,
    GameStatus,
    GameType,
    LedgerEntryType,
    RainStatus,
    TransactionStatus,
    TransactionType,
    UserRole,
)
from casino.utils.money import SolAmount


HOUSE_USER_ID = "HOUSE"


def is_house_seat(player_id: str) -> bool:
    return player_id == HOUSE_USER_ID or player_id.startswith(f"{HOUSE_USER_ID}:")


@dataclass(frozen=True, slots=True)
class DiceRollRecord:
    player_id: str
    round_index: int
    roll_index: int
    die_a: int
    die_b: int
    scores: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.die_a + self.die_b


@dataclass(frozen=True, slots=True)
class DiceTurnEvent:
    roll: DiceRollRecord
    round_winner_id: str | None = None
    round_tied: bool = False
    match_over: bool = False
    roll_off_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DicePlayResult:
    game: GameRecord
    events: list[DiceTurnEvent]


@dataclass(slots=True)
class UserRecord:
    discord_id: str
    sol_balance_lamports: int = 0
    total_deposited_lamports: int = 0
    total_withdrawn_lamports: int = 0
    total_wagered_lamports: int = 0
    total_won_lamports: int = 0
    total_fees_paid_lamports: int = 0
    total_wagered_usd: Decimal = Decimal("0")
    total_won_usd: Decimal = Decimal("0")
    max_bet_usd: Decimal = Decimal("0")
    games_played: int = 0
    games_won: int = 0
    display_currency: str = "USD"
    wallet_address: str | None = None
    deposit_address: str | None = None
    deposit_memo: str = ""
    roles: list[UserRole] = field(default_factory=lambda: [UserRole.PLAYER])
    referred_by: str | None = None
    referred_at: datetime | None = None
    referred_wagered_usd: Decimal = Decimal("0")
    referred_wagered_lamports: int = 0
    affiliate_earned_lamports: int = 0
    affiliate_earned_usd: Decimal = Decimal("0")
    version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def sol_balance(self) -> SolAmount:
        return SolAmount.from_lamports(self.sol_balance_lamports)

    def has_role(self, role: UserRole) -> bool:
        return role in self.roles


@dataclass(frozen=True, slots=True)
class PlayerProfileStats:
    games_played: int = 0
    games_won: int = 0
    wagered_usd: Decimal = Decimal("0")
    wagered_lamports: int = 0
    profit_usd: Decimal = Decimal("0")
    profit_lamports: int = 0
    favorite_game: str = "Coinflip"


@dataclass(slots=True)
class GameRecord:
    game_id: str
    status: GameStatus
    creator_id: str
    bet_amount_lamports: int
    currency: str
    bet_amount_fiat: Decimal
    bet_amount_usd: Decimal = Decimal("0")
    player_wager_lamports: dict[str, int] = field(default_factory=dict)
    game_type: GameType = GameType.COINFLIP
    mode: GameMode = GameMode.SOLO
    heads_ids: list[str] = field(default_factory=list)
    tails_ids: list[str] = field(default_factory=list)
    house_seat_ids: list[str] = field(default_factory=list)
    ready_ids: list[str] = field(default_factory=list)
    winner_id: str | None = None
    winner_ids: list[str] = field(default_factory=list)
    winner_side: CoinSide | None = None
    house_fee_lamports: int = 0
    house_fee_usd: Decimal = Decimal("0")
    winner_payout_lamports: int = 0
    per_winner_payout_lamports: int = 0
    coin_result: CoinSide | None = None
    coin_flips: list[CoinSide] = field(default_factory=list)
    version: int = 0
    guild_id: str | None = None
    channel_id: str | None = None
    message_id: str | None = None
    private_channel_id: str | None = None
    private_message_id: str | None = None
    server_seed_hash: str = ""
    server_seed: str | None = None
    client_seed: str = ""
    nonce: int = 0
    ledger_ids: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    expires_at: datetime | None = None
    auto_flip_at: datetime | None = None
    table_number: int | None = None
    game_number: int | None = None
    rematch_of_game_id: str | None = None
    invited_ids: list[str] = field(default_factory=list)
    channel_delete_at: datetime | None = None
    max_players: int = 2
    first_to: int | None = None
    scores: dict[str, int] = field(default_factory=dict)
    turn_index: int = 0
    round_index: int = 1
    roll_index: int = 0
    current_round_rolls: list[DiceRollRecord] = field(default_factory=list)
    dice_rolls: list[DiceRollRecord] = field(default_factory=list)
    dice_rolling_ids: list[str] = field(default_factory=list)

    @property
    def bet_amount(self) -> SolAmount:
        return SolAmount.from_lamports(self.bet_amount_lamports)

    @property
    def player1_id(self) -> str:
        return self.creator_id

    @property
    def player1_side(self) -> CoinSide:
        return CoinSide.HEADS if self.creator_id in self.heads_ids else CoinSide.TAILS

    @property
    def player2_id(self) -> str | None:
        for player_id in self.player_ids():
            if player_id != self.creator_id:
                return player_id
        return None

    def seats_per_side(self) -> int:
        return self.mode.seats_per_side()

    def is_dice(self) -> bool:
        return self.game_type is GameType.DICE

    def is_awaiting_settlement(self) -> bool:
        return not self.is_dice() and self.status is GameStatus.ACTIVE

    def is_dice_team(self) -> bool:
        return self.is_dice() and self.mode is GameMode.TEAM

    def is_dice_ffa(self) -> bool:
        return self.is_dice() and self.mode is not GameMode.TEAM

    def seat_capacity(self) -> int:
        if self.is_dice_ffa():
            return max(int(self.max_players), 2)
        if self.is_dice_team():
            return 4
        return self.seats_per_side()

    def join_side_capacity(self) -> int:
        if self.is_dice_ffa():
            return self.seat_capacity()
        return self.seats_per_side()

    def matchup_label(self) -> str:
        if self.mode is GameMode.TEAM:
            return "2v2"
        if self.is_dice():
            return "v".join(["1"] * self.seat_capacity())
        return "1v1"

    def side_of(self, player_id: str) -> CoinSide | None:
        if player_id in self.heads_ids:
            return CoinSide.HEADS
        if player_id in self.tails_ids:
            return CoinSide.TAILS
        return None

    def team_label(self, side: CoinSide) -> str:
        return "Team 1" if side is CoinSide.HEADS else "Team 2"

    def house_game_pnl_fiat(self) -> Decimal:
        house_seats = [player_id for player_id in self.player_ids() if is_house_seat(player_id)]
        wagered = self.bet_amount_fiat * Decimal(len(house_seats))
        won = self.winner_gross_share_fiat() * Decimal(len(self.house_winner_ids()))
        return won - wagered

    def house_net_fiat(self) -> Decimal:
        return self.house_fee_usd + self.house_game_pnl_fiat()

    def ids_for(self, side: CoinSide) -> list[str]:
        return self.heads_ids if side is CoinSide.HEADS else self.tails_ids

    def player_ids(self) -> list[str]:
        return [*self.heads_ids, *self.tails_ids]

    def rolling_player_ids(self) -> list[str]:
        seated = self.player_ids()
        if not self.dice_rolling_ids:
            return seated
        allowed = set(self.dice_rolling_ids)
        return [player_id for player_id in seated if player_id in allowed]

    def is_dice_roll_off(self) -> bool:
        return bool(self.dice_rolling_ids)

    def current_turn_id(self) -> str | None:
        players = self.rolling_player_ids()
        if not players:
            return None
        return players[self.turn_index % len(players)]

    def score_for(self, player_id: str) -> int:
        return int(self.scores.get(player_id, 0))

    def side_score(self, side: CoinSide) -> int:
        return int(self.scores.get(side.value, 0))

    def flip_series(self) -> list[CoinSide]:
        if self.coin_flips:
            return list(self.coin_flips)
        if self.coin_result is not None:
            return [self.coin_result]
        return []

    def has_reached_first_to(self) -> bool:
        target = self.first_to or 1
        return any(score >= target for score in self.scores.values())

    def human_player_ids(self) -> list[str]:
        return [player_id for player_id in self.player_ids() if not is_house_seat(player_id)]

    def house_winner_ids(self) -> list[str]:
        return [player_id for player_id in self.winner_ids if is_house_seat(player_id)]

    def human_winner_ids(self) -> list[str]:
        return [player_id for player_id in self.winner_ids if not is_house_seat(player_id)]

    def house_gross_payout_lamports(self) -> int:
        """Full pot share returned to winning house seats, before any display fee."""
        return self.winner_gross_share_lamports() * len(self.house_winner_ids())

    def winner_gross_share_lamports(self) -> int:
        winners = max(len(self.winner_ids), 1)
        seats = len(self.player_ids())
        if seats < 1:
            return 0
        return (self.bet_amount_lamports * seats) // winners

    def winner_gross_share_fiat(self) -> Decimal:
        winners = max(len(self.winner_ids), 1)
        seats = max(len(self.player_ids()), 1)
        return (self.bet_amount_fiat * seats) / Decimal(winners)

    def next_house_seat_id(self) -> str:
        count = self.house_seat_count()
        return HOUSE_USER_ID if count == 0 else f"{HOUSE_USER_ID}:{count + 1}"

    def next_vacant_side(self) -> CoinSide | None:
        if self.is_dice_ffa():
            return None if self.is_full() else CoinSide.HEADS
        if self.vacant_on(CoinSide.HEADS):
            return CoinSide.HEADS
        if self.vacant_on(CoinSide.TAILS):
            return CoinSide.TAILS
        return None

    def has_player(self, player_id: str) -> bool:
        return player_id in self.heads_ids or player_id in self.tails_ids

    def is_full(self) -> bool:
        if self.is_dice_ffa():
            return len(self.player_ids()) >= self.seat_capacity()
        seats = self.seats_per_side()
        return len(self.heads_ids) >= seats and len(self.tails_ids) >= seats

    def vacant_on(self, side: CoinSide) -> bool:
        if self.is_dice_ffa():
            return side is CoinSide.HEADS and len(self.heads_ids) < self.seat_capacity()
        return len(self.ids_for(side)) < self.seats_per_side()

    def house_seat_count(self) -> int:
        return len(self.house_seat_ids)

    def can_call_house_seat(self, *, max_house_usd: Decimal, seat_usd: Decimal) -> bool:
        if self.status is not GameStatus.PENDING:
            return False
        if self.is_full():
            return False
        if not self.is_dice_ffa() and self.next_vacant_side() is None:
            return False
        return seat_usd * Decimal(self.house_seat_count() + 1) <= max_house_usd

    def display_title(self) -> str:
        name = self.game_type.value.title()
        if self.game_number is None:
            return name
        return f"{name} #{self.game_number}"

    def player_won(self, player_id: str) -> bool:
        return player_id in self.winner_ids or self.winner_id == player_id

    def stake_usd(self) -> Decimal:
        if self.bet_amount_usd > 0:
            return self.bet_amount_usd
        return self.bet_amount_fiat

    def wager_for(self, player_id: str) -> SolAmount:
        stored = self.player_wager_lamports.get(player_id)
        if stored is None:
            return self.bet_amount
        return SolAmount.from_lamports(stored)

    def record_wager(self, player_id: str, lamports: int) -> None:
        self.player_wager_lamports[player_id] = lamports

    def player_profit_fiat(self, player_id: str) -> Decimal:
        stake = self.stake_usd()
        if not self.player_won(player_id):
            return -stake
        winners = max(len(self.winner_ids), 1)
        if is_house_seat(player_id):
            payout = self.winner_gross_share_fiat()
        else:
            pot = stake * Decimal(max(len(self.player_ids()), 1))
            payout = (pot - self.house_fee_usd) / Decimal(winners)
        return payout - stake

    def player_profit_lamports(self, player_id: str) -> int:
        stake = self.wager_for(player_id).lamports
        if not self.player_won(player_id):
            return -stake
        if is_house_seat(player_id):
            return self.winner_gross_share_lamports() - stake
        return self.per_winner_payout_lamports - stake

    def is_ready(self, player_id: str) -> bool:
        return player_id in self.ready_ids

    def all_humans_ready(self) -> bool:
        humans = set(self.human_player_ids())
        return bool(humans) and humans <= set(self.ready_ids)

    def can_start(self) -> bool:
        return self.is_full() and self.all_humans_ready() and not self.is_awaiting_rematch_accepts()

    def is_awaiting_rematch_accepts(self) -> bool:
        return bool(self.rematch_of_game_id) and any(not self.has_player(player_id) for player_id in self.invited_ids)


@dataclass(slots=True)
class GuildConfigRecord:
    guild_id: str
    coinflip_outcomes_channel_id: str | None = None
    coinflip_category_id: str | None = None
    dice_outcomes_channel_id: str | None = None
    dice_category_id: str | None = None
    provably_fair_channel_id: str | None = None
    open_games_channel_id: str | None = None
    open_coinflip_channel_id: str | None = None
    open_dice_channel_id: str | None = None
    deposit_alerts_channel_id: str | None = None
    withdrawal_alerts_channel_id: str | None = None
    house_ledger_channel_id: str | None = None
    rain_channel_id: str | None = None
    rain_ping_role_id: str | None = None
    verify_channel_id: str | None = None
    member_role_id: str | None = None
    provably_fair_message_id: str | None = None
    verify_message_id: str | None = None
    version: int = 0
    updated_at: datetime | None = None

    def outcomes_channel_id(self, game_type: GameType) -> str | None:
        if game_type is GameType.DICE:
            return self.dice_outcomes_channel_id
        return self.coinflip_outcomes_channel_id

    def listing_channel_id(self, game_type: GameType) -> str | None:
        if game_type is GameType.DICE:
            return self.open_dice_channel_id or self.open_games_channel_id
        return self.open_coinflip_channel_id or self.open_games_channel_id

    def category_id(self, game_type: GameType) -> str | None:
        if game_type is GameType.DICE:
            return self.dice_category_id
        return self.coinflip_category_id


@dataclass(slots=True)
class RainRecord:
    rain_id: str
    status: RainStatus
    creator_id: str
    amount_lamports: int
    currency: str
    amount_fiat: Decimal
    guild_id: str | None = None
    joiner_ids: list[str] = field(default_factory=list)
    channel_id: str | None = None
    message_id: str | None = None
    server_seed_hash: str = ""
    server_seed: str | None = None
    client_seed: str = ""
    nonce: int = 0
    ledger_ids: list[str] = field(default_factory=list)
    payouts_lamports: dict[str, int] = field(default_factory=dict)
    fair_digest: str = ""
    version: int = 0
    created_at: datetime | None = None
    expires_at: datetime | None = None
    resolved_at: datetime | None = None

    def has_joined(self, player_id: str) -> bool:
        return player_id in self.joiner_ids

    def joiner_count(self) -> int:
        return len(self.joiner_ids)

    def is_open(self) -> bool:
        return self.status is RainStatus.PENDING


@dataclass(slots=True)
class TransactionRecord:
    transaction_id: str
    type: TransactionType
    status: TransactionStatus
    amount_lamports: int
    amount_usd: Decimal
    user_id: str | None = None
    game_id: str | None = None
    solana_tx_signature: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(slots=True)
class LedgerRecord:
    entry_id: str
    user_id: str
    type: LedgerEntryType
    amount_lamports: int
    balance_after_lamports: int
    price_usd_at_time: Decimal
    description: str
    related_game_id: str | None = None
    related_tx_id: str | None = None
    timestamp: datetime | None = None


@dataclass(slots=True)
class HouseTreasuryRecord:
    sol_balance_lamports: int = 0
    total_fees_collected_lamports: int = 0
    version: int = 0
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TimeframeStats:
    label: str
    games_completed: int
    unique_players: int
    volume_lamports: int
    volume_usd: Decimal
    fees_lamports: int
    fees_usd: Decimal
    call_bot_games: int
    house_wagered_lamports: int
    house_payout_lamports: int
    house_net_lamports: int


@dataclass(frozen=True, slots=True)
class HouseStatsReport:
    house_balance_lamports: int
    super_wallet_lamports: int
    lifetime_fees_lamports: int
    open_tables: int
    sol_price_usd: Decimal
    timeframes: tuple[TimeframeStats, ...]


@dataclass(slots=True)
class CursorRecord:
    name: str
    value: str
    updated_at: datetime | None = None
