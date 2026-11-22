from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from casino.domain.blackjack import BlackjackAction, BlackjackCard, BlackjackHand, legal_actions
from casino.domain.baccarat import BaccaratSide
from casino.domain.enums import (
    BaccaratStatus,
    CoinSide,
    GameMode,
    GameStatus,
    GameType,
    JackpotStatus,
    LedgerEntryType,
    RainStatus,
    TicketCategory,
    TicketStatus,
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
    investor_shares: int = 0
    fair_client_seed: str = ""
    next_server_seed: str = ""
    next_server_seed_hash: str = ""
    fair_nonce: int = 0
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
    client_seeds: dict[str, str] = field(default_factory=dict)
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
    blackjack_player_hands: list[BlackjackHand] = field(default_factory=list)
    blackjack_dealer_cards: list[BlackjackCard] = field(default_factory=list)
    blackjack_hole_hidden: bool = True
    blackjack_hand_index: int = 0
    blackjack_card_index: int = 0
    target_picks: dict[str, int] = field(default_factory=dict)
    target_number: int | None = None
    limbo_target: Decimal | None = None
    limbo_result: Decimal | None = None
    mines_count: int = 0
    mines_tiles: list[int] = field(default_factory=list)
    mines_revealed: list[int] = field(default_factory=list)
    mines_hit: int | None = None

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

    def is_blackjack(self) -> bool:
        return self.game_type is GameType.BLACKJACK

    def is_target(self) -> bool:
        return self.game_type is GameType.TARGET

    def is_limbo(self) -> bool:
        return self.game_type is GameType.LIMBO

    def is_mines(self) -> bool:
        return self.game_type is GameType.MINES

    def is_jackpot(self) -> bool:
        return self.game_type is GameType.JACKPOT

    def is_baccarat(self) -> bool:
        return self.game_type is GameType.BACCARAT

    def is_vs_house(self) -> bool:
        return self.is_blackjack() or self.is_limbo() or self.is_mines() or self.is_baccarat()

    def current_blackjack_hand(self) -> BlackjackHand | None:
        if not self.blackjack_player_hands:
            return None
        index = min(max(self.blackjack_hand_index, 0), len(self.blackjack_player_hands) - 1)
        return self.blackjack_player_hands[index]

    def blackjack_legal_actions(self) -> frozenset[BlackjackAction]:
        if self.status is not GameStatus.ACTIVE or not self.is_blackjack():
            return frozenset()
        return legal_actions(self.blackjack_player_hands, self.blackjack_hand_index)

    def is_awaiting_settlement(self) -> bool:
        return (
            not self.is_dice()
            and not self.is_blackjack()
            and not self.is_target()
            and not self.is_limbo()
            and not self.is_mines()
            and self.status is GameStatus.ACTIVE
        )

    def is_dice_team(self) -> bool:
        return self.is_dice() and self.mode is GameMode.TEAM

    def is_dice_ffa(self) -> bool:
        return self.is_dice() and self.mode is not GameMode.TEAM

    def is_open_ffa(self) -> bool:
        return self.mode is not GameMode.TEAM and (self.is_dice() or self.is_target())

    def seat_capacity(self) -> int:
        if self.is_open_ffa():
            return max(int(self.max_players), 2)
        if self.is_dice_team():
            return 4
        return self.seats_per_side()

    def join_side_capacity(self) -> int:
        if self.is_open_ffa():
            return self.seat_capacity()
        return self.seats_per_side()

    def matchup_label(self) -> str:
        if self.mode is GameMode.TEAM:
            return "2v2"
        if self.is_open_ffa():
            return "v".join(["1"] * self.seat_capacity())
        if self.is_blackjack() or self.is_limbo() or self.is_mines():
            return "vs House"
        if self.is_jackpot():
            count = max(len(self.player_ids()), 1)
            return f"{count}p"
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
        if self.is_vs_house():
            if not self.bet_amount_lamports:
                return Decimal("0")
            return (
                self.stake_usd()
                * Decimal(self.house_game_pnl_lamports())
                / Decimal(self.bet_amount_lamports)
            )
        house_seats = [player_id for player_id in self.player_ids() if is_house_seat(player_id)]
        wagered = self.bet_amount_fiat * Decimal(len(house_seats))
        won = self.winner_gross_share_fiat() * Decimal(len(self.house_winner_ids()))
        return won - wagered

    def house_game_pnl_lamports(self) -> int:
        if self.is_vs_house():
            stake = sum(self.wager_for(player_id).lamports for player_id in self.human_player_ids())
            return stake - int(self.winner_payout_lamports)
        house_seats = [player_id for player_id in self.player_ids() if is_house_seat(player_id)]
        if not house_seats:
            return 0
        wagered = sum(self.wager_for(player_id).lamports for player_id in house_seats)
        return self.house_gross_payout_lamports() - wagered

    def human_volume_lamports(self) -> int:
        return sum(self.wager_for(player_id).lamports for player_id in self.human_player_ids())

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
        if self.is_open_ffa():
            return None if self.is_full() else CoinSide.HEADS
        if self.vacant_on(CoinSide.HEADS):
            return CoinSide.HEADS
        if self.vacant_on(CoinSide.TAILS):
            return CoinSide.TAILS
        return None

    def has_player(self, player_id: str) -> bool:
        return player_id in self.heads_ids or player_id in self.tails_ids

    def is_full(self) -> bool:
        if self.is_open_ffa():
            return len(self.player_ids()) >= self.seat_capacity()
        seats = self.seats_per_side()
        return len(self.heads_ids) >= seats and len(self.tails_ids) >= seats

    def vacant_on(self, side: CoinSide) -> bool:
        if self.is_open_ffa():
            return side is CoinSide.HEADS and len(self.heads_ids) < self.seat_capacity()
        return len(self.ids_for(side)) < self.seats_per_side()

    def house_seat_count(self) -> int:
        return len(self.house_seat_ids)

    def can_call_house_seat(self, *, max_house: int | Decimal, seat: int | Decimal) -> bool:
        if self.status is not GameStatus.PENDING:
            return False
        if self.is_full():
            return False
        if not self.is_open_ffa() and self.next_vacant_side() is None:
            return False
        return seat * (self.house_seat_count() + 1) <= max_house

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
        if not self.winner_ids:
            return Decimal("0")
        if self.is_limbo() or self.is_mines() or self.is_jackpot() or self.is_baccarat():
            payout = self._vs_house_payout_fiat()
            if self.is_baccarat():
                return payout - stake
            if is_house_seat(player_id):
                return stake if self.player_won(player_id) else stake - payout
            return payout - stake if self.player_won(player_id) else -stake
        if not self.player_won(player_id):
            return -stake
        winners = max(len(self.winner_ids), 1)
        if is_house_seat(player_id):
            payout = self.winner_gross_share_fiat()
        else:
            pot = stake * Decimal(max(len(self.player_ids()), 1))
            payout = (pot - self.house_fee_usd) / Decimal(winners)
        return payout - stake

    def _vs_house_payout_fiat(self) -> Decimal:
        if self.bet_amount_lamports <= 0:
            return Decimal("0")
        return (
            self.stake_usd()
            * Decimal(int(self.winner_payout_lamports))
            / Decimal(self.bet_amount_lamports)
        )

    def player_profit_lamports(self, player_id: str) -> int:
        stake = self.wager_for(player_id).lamports
        if self.is_baccarat():
            return int(self.per_winner_payout_lamports) - stake
        if not self.winner_ids:
            return 0
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
        if self.is_target() or self.is_limbo() or self.is_mines():
            return False
        return self.is_full() and self.all_humans_ready() and not self.is_awaiting_rematch_accepts()

    def has_target_pick(self, player_id: str) -> bool:
        return player_id in self.target_picks

    def target_ready_to_resolve(self) -> bool:
        if not self.is_target() or not self.is_full():
            return False
        return all(player_id in self.target_picks for player_id in self.player_ids())

    def is_awaiting_rematch_accepts(self) -> bool:
        return bool(self.rematch_of_game_id) and any(not self.has_player(player_id) for player_id in self.invited_ids)


@dataclass(slots=True)
class GuildConfigRecord:
    guild_id: str
    coinflip_outcomes_channel_id: str | None = None
    coinflip_category_id: str | None = None
    dice_outcomes_channel_id: str | None = None
    dice_category_id: str | None = None
    blackjack_outcomes_channel_id: str | None = None
    target_outcomes_channel_id: str | None = None
    provably_fair_channel_id: str | None = None
    open_games_channel_id: str | None = None
    open_coinflip_channel_id: str | None = None
    open_dice_channel_id: str | None = None
    open_blackjack_channel_id: str | None = None
    open_target_channel_id: str | None = None
    deposit_alerts_channel_id: str | None = None
    withdrawal_alerts_channel_id: str | None = None
    house_ledger_channel_id: str | None = None
    rain_channel_id: str | None = None
    rain_ping_role_id: str | None = None
    verify_channel_id: str | None = None
    member_role_id: str | None = None
    investor_role_id: str | None = None
    support_channel_id: str | None = None
    support_category_id: str | None = None
    support_role_id: str | None = None
    support_logs_channel_id: str | None = None
    support_message_id: str | None = None
    provably_fair_message_id: str | None = None
    verify_message_id: str | None = None
    jackpot_channel_id: str | None = None
    jackpot_history_channel_id: str | None = None
    jackpot_message_id: str | None = None
    baccarat_channel_id: str | None = None
    baccarat_history_channel_id: str | None = None
    baccarat_message_id: str | None = None
    version: int = 0
    updated_at: datetime | None = None

    def outcomes_channel_id(self, game_type: GameType) -> str | None:
        if game_type is GameType.DICE:
            return self.dice_outcomes_channel_id
        if game_type is GameType.BLACKJACK:
            return self.blackjack_outcomes_channel_id
        if game_type is GameType.TARGET:
            return self.target_outcomes_channel_id
        if game_type is GameType.LIMBO or game_type is GameType.MINES:
            return self.blackjack_outcomes_channel_id
        if game_type is GameType.JACKPOT:
            return self.jackpot_history_channel_id
        if game_type is GameType.BACCARAT:
            return self.baccarat_history_channel_id
        return self.coinflip_outcomes_channel_id

    def listing_channel_id(self, game_type: GameType) -> str | None:
        if game_type is GameType.DICE:
            return self.open_dice_channel_id or self.open_games_channel_id
        if game_type is GameType.BLACKJACK:
            return None
        if game_type is GameType.TARGET:
            return None
        if game_type is GameType.LIMBO or game_type is GameType.MINES:
            return None
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
class JackpotRecord:
    jackpot_id: str
    status: JackpotStatus
    guild_id: str
    opener_id: str | None = None
    entries_lamports: dict[str, int] = field(default_factory=dict)
    channel_id: str | None = None
    message_id: str | None = None
    history_message_id: str | None = None
    winner_id: str | None = None
    winner_payout_lamports: int = 0
    house_fee_lamports: int = 0
    ticket: int | None = None
    server_seed_hash: str = ""
    server_seed: str | None = None
    client_seed: str = ""
    client_seeds: dict[str, str] = field(default_factory=dict)
    nonce: int = 0
    fair_digest: str = ""
    ledger_ids: list[str] = field(default_factory=list)
    version: int = 0
    created_at: datetime | None = None
    expires_at: datetime | None = None
    reveal_until: datetime | None = None
    resolved_at: datetime | None = None

    def pot_lamports(self) -> int:
        return sum(int(value) for value in self.entries_lamports.values())

    def opener_lamports(self) -> int | None:
        if self.opener_id is None:
            return None
        if self.opener_id not in self.entries_lamports:
            return None
        return int(self.entries_lamports[self.opener_id])

    def has_joined(self, player_id: str) -> bool:
        return player_id in self.entries_lamports

    def player_ids(self) -> list[str]:
        return list(self.entries_lamports)

    def is_open(self) -> bool:
        return self.status is JackpotStatus.OPEN

    def is_showing(self) -> bool:
        return self.status is JackpotStatus.SHOWING


@dataclass(slots=True)
class BaccaratRecord:
    baccarat_id: str
    status: BaccaratStatus
    guild_id: str
    opener_id: str | None = None
    bets_lamports: dict[str, int] = field(default_factory=dict)
    bets_sides: dict[str, str] = field(default_factory=dict)
    reserved_lamports: dict[str, int] = field(default_factory=dict)
    payouts_lamports: dict[str, int] = field(default_factory=dict)
    channel_id: str | None = None
    message_id: str | None = None
    history_message_id: str | None = None
    outcome: BaccaratSide | None = None
    player_cards: list[BlackjackCard] = field(default_factory=list)
    banker_cards: list[BlackjackCard] = field(default_factory=list)
    house_pnl_lamports: int = 0
    server_seed_hash: str = ""
    server_seed: str | None = None
    client_seed: str = ""
    client_seeds: dict[str, str] = field(default_factory=dict)
    nonce: int = 0
    fair_digest: str = ""
    ledger_ids: list[str] = field(default_factory=list)
    version: int = 0
    created_at: datetime | None = None
    expires_at: datetime | None = None
    reveal_until: datetime | None = None
    resolved_at: datetime | None = None

    def pot_lamports(self) -> int:
        return sum(int(value) for value in self.bets_lamports.values())

    def reserved_total(self) -> int:
        return sum(int(value) for value in self.reserved_lamports.values())

    def has_bet(self, player_id: str) -> bool:
        return player_id in self.bets_lamports

    def side_of(self, player_id: str) -> BaccaratSide | None:
        raw = self.bets_sides.get(player_id)
        if not raw:
            return None
        return BaccaratSide(raw)

    def player_ids(self) -> list[str]:
        return list(self.bets_lamports)

    def is_open(self) -> bool:
        return self.status is BaccaratStatus.OPEN

    def is_showing(self) -> bool:
        return self.status is BaccaratStatus.SHOWING


@dataclass(slots=True)
class TicketRecord:
    ticket_id: str
    guild_id: str
    opener_id: str
    category: TicketCategory
    status: TicketStatus = TicketStatus.OPEN
    number: int = 0
    subject: str = ""
    body: str = ""
    channel_id: str | None = None
    claimed_by: str | None = None
    participant_ids: list[str] = field(default_factory=list)
    close_reason: str = ""
    chat_log: list[dict] = field(default_factory=list)
    version: int = 0
    created_at: datetime | None = None
    claimed_at: datetime | None = None
    closed_at: datetime | None = None
    closed_by: str | None = None

    def is_open(self) -> bool:
        return self.status is not TicketStatus.CLOSED

    def can_view(self, user_id: str) -> bool:
        return user_id in {self.opener_id, self.claimed_by, *self.participant_ids}


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
    founder_shares: int = 0
    investor_shares: int = 0
    stats_reset_at: datetime | None = None
    version: int = 0
    updated_at: datetime | None = None

    def pool_shares(self) -> int:
        return self.founder_shares + self.investor_shares


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
    deposits_usd: Decimal = Decimal("0")
    withdrawals_usd: Decimal = Decimal("0")
    house_games_pnl_usd: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class HouseStatsReport:
    house_balance_lamports: int
    super_wallet_lamports: int
    lifetime_fees_lamports: int
    open_tables: int
    sol_price_usd: Decimal
    timeframes: tuple[TimeframeStats, ...]
    founder_shares: int = 0
    investor_shares: int = 0
    founder_pool_lamports: int = 0
    investor_pool_lamports: int = 0


@dataclass(slots=True)
class CursorRecord:
    name: str
    value: str
    updated_at: datetime | None = None
