from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from casino.domain.enums import CoinSide, GameStatus, RainStatus, UserRole
from casino.domain.records import (
    HOUSE_USER_ID,
    GameRecord,
    is_house_seat,
    HouseTreasuryRecord,
    LedgerRecord,
    RainRecord,
    TransactionRecord,
    UserRecord,
)
from casino.errors import AlreadyProcessedError, InsufficientBalanceError, InsufficientHouseReserveError, NotFoundError
from casino.utils.money import SolAmount


class MemoryUserRepository:
    def __init__(self) -> None:
        self.users: dict[str, UserRecord] = {}

    async def find_by_discord_id(self, discord_id: str, session=None) -> UserRecord | None:
        user = self.users.get(discord_id)
        return self._clone(user) if user else None

    async def find_by_deposit_memo(self, memo: str, session=None) -> UserRecord | None:
        for user in self.users.values():
            if user.deposit_memo == memo:
                return self._clone(user)
        return None

    async def find_by_deposit_address(self, address: str, session=None) -> UserRecord | None:
        for user in self.users.values():
            if user.deposit_address == address:
                return self._clone(user)
        return None

    async def list_deposit_wallets(self, session=None) -> list[tuple[str, str]]:
        return [
            (user.discord_id, user.deposit_address)
            for user in self.users.values()
            if user.deposit_address
        ]

    async def insert(self, user: UserRecord, session=None) -> None:
        self.users[user.discord_id] = user

    async def credit_balance(
        self,
        discord_id: str,
        amount: SolAmount,
        *,
        deposited: SolAmount | None = None,
        won: SolAmount | None = None,
        fees_paid: SolAmount | None = None,
        games_played: int = 0,
        games_won: int = 0,
        now: datetime,
        session=None,
    ) -> UserRecord:
        user = self.users[discord_id]
        user.sol_balance_lamports += amount.lamports
        if deposited:
            user.total_deposited_lamports += deposited.lamports
        if won:
            user.total_won_lamports += won.lamports
        if fees_paid:
            user.total_fees_paid_lamports += fees_paid.lamports
        user.games_played += games_played
        user.games_won += games_won
        user.version += 1
        user.updated_at = now
        return self._clone(user)

    async def debit_balance(
        self,
        discord_id: str,
        amount: SolAmount,
        *,
        wagered: SolAmount | None = None,
        withdrawn: SolAmount | None = None,
        now: datetime,
        session=None,
    ) -> UserRecord:
        user = self.users.get(discord_id)
        if user is None:
            raise NotFoundError("User was not found.")
        if user.sol_balance_lamports < amount.lamports:
            raise InsufficientBalanceError()
        user.sol_balance_lamports -= amount.lamports
        if wagered:
            user.total_wagered_lamports += wagered.lamports
        if withdrawn:
            user.total_withdrawn_lamports += withdrawn.lamports
        user.version += 1
        user.updated_at = now
        return self._clone(user)

    async def set_wallet(self, discord_id: str, wallet: str, now: datetime, session=None) -> UserRecord | None:
        user = self.users.get(discord_id)
        if user is None:
            return None
        user.wallet_address = wallet
        user.updated_at = now
        user.version += 1
        return self._clone(user)

    async def set_deposit_address(self, discord_id: str, address: str, now: datetime, session=None) -> UserRecord | None:
        user = self.users.get(discord_id)
        if user is None:
            return None
        user.deposit_address = address
        user.updated_at = now
        user.version += 1
        return self._clone(user)

    async def apply_game_stats(
        self,
        discord_id: str,
        *,
        wagered: SolAmount,
        wagered_usd: Decimal,
        max_bet_usd: Decimal,
        fees_paid: SolAmount,
        won: bool,
        now: datetime,
        session=None,
        won_usd: Decimal = Decimal("0"),
    ) -> UserRecord:
        user = self.users[discord_id]
        user.games_played += 1
        if won:
            user.games_won += 1
        user.total_wagered_lamports += wagered.lamports
        user.total_fees_paid_lamports += fees_paid.lamports
        user.total_wagered_usd += wagered_usd
        user.total_won_usd += won_usd
        if max_bet_usd > user.max_bet_usd:
            user.max_bet_usd = max_bet_usd
        if user.referred_by:
            user.referred_wagered_usd += wagered_usd
            user.referred_wagered_lamports += wagered.lamports
        user.updated_at = now
        return self._clone(user)

    async def bind_referrer(self, discord_id: str, referrer_id: str, now: datetime, session=None) -> UserRecord | None:
        user = self.users.get(discord_id)
        if user is None or user.referred_by:
            return None
        user.referred_by = referrer_id
        user.referred_at = now
        user.referred_wagered_usd = Decimal("0")
        user.referred_wagered_lamports = 0
        user.updated_at = now
        user.version += 1
        return self._clone(user)

    async def list_referred_by(self, referrer_id: str, session=None) -> list[UserRecord]:
        referred = [
            self._clone(user)
            for user in self.users.values()
            if user.referred_by == referrer_id
        ]
        referred.sort(key=lambda user: user.referred_at.isoformat() if user.referred_at else "")
        return referred

    async def add_affiliate_earnings(
        self,
        discord_id: str,
        amount: SolAmount,
        earned_usd: Decimal,
        now: datetime,
        session=None,
    ) -> UserRecord | None:
        user = self.users.get(discord_id)
        if user is None:
            return None
        user.affiliate_earned_lamports += amount.lamports
        user.affiliate_earned_usd += earned_usd
        user.updated_at = now
        user.version += 1
        return self._clone(user)

    def _clone(self, user: UserRecord) -> UserRecord:
        return UserRecord(
            discord_id=user.discord_id,
            sol_balance_lamports=user.sol_balance_lamports,
            total_deposited_lamports=user.total_deposited_lamports,
            total_withdrawn_lamports=user.total_withdrawn_lamports,
            total_wagered_lamports=user.total_wagered_lamports,
            total_won_lamports=user.total_won_lamports,
            total_fees_paid_lamports=user.total_fees_paid_lamports,
            total_wagered_usd=user.total_wagered_usd,
            total_won_usd=user.total_won_usd,
            max_bet_usd=user.max_bet_usd,
            games_played=user.games_played,
            games_won=user.games_won,
            display_currency=user.display_currency,
            wallet_address=user.wallet_address,
            deposit_address=user.deposit_address,
            deposit_memo=user.deposit_memo,
            roles=list(user.roles),
            referred_by=user.referred_by,
            referred_at=user.referred_at,
            referred_wagered_usd=user.referred_wagered_usd,
            referred_wagered_lamports=user.referred_wagered_lamports,
            affiliate_earned_lamports=user.affiliate_earned_lamports,
            affiliate_earned_usd=user.affiliate_earned_usd,
            version=user.version,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )


class MemoryGameRepository:
    def __init__(self) -> None:
        self.games: dict[str, GameRecord] = {}

    async def insert(self, game: GameRecord, session=None) -> None:
        self.games[game.game_id] = game

    async def find_by_id(self, game_id: str, session=None) -> GameRecord | None:
        game = self.games.get(game_id)
        return self._clone(game) if game else None

    async def list_by_player(self, discord_id: str, limit: int = 10, skip: int = 0) -> list[GameRecord]:
        matches = [
            self._clone(game)
            for game in self.games.values()
            if game.status is GameStatus.COMPLETED and game.has_player(discord_id)
        ]
        matches.sort(key=lambda game: game.resolved_at or game.created_at or datetime.min, reverse=True)
        return matches[skip : skip + limit]

    async def count_completed_by_player(self, discord_id: str) -> int:
        return sum(
            1
            for game in self.games.values()
            if game.status is GameStatus.COMPLETED and game.has_player(discord_id)
        )

    async def list_completed_by_player(self, discord_id: str) -> list[GameRecord]:
        return await self.list_by_player(discord_id, limit=10_000, skip=0)

    async def try_add_to_side(
        self,
        game_id: str,
        player_id: str,
        side: CoinSide,
        seats_per_side: int,
        *,
        house_seat: bool = False,
        session=None,
    ) -> GameRecord | None:
        game = self.games.get(game_id)
        if game is None or game.status != GameStatus.PENDING:
            return None
        if game.has_player(player_id):
            return None
        target = game.heads_ids if side is CoinSide.HEADS else game.tails_ids
        if len(target) >= seats_per_side:
            return None
        target.append(player_id)
        if house_seat or is_house_seat(player_id):
            game.house_seat_ids.append(player_id)
        game.version += 1
        return self._clone(game)

    async def try_mark_ready(self, game_id: str, player_id: str, session=None) -> GameRecord | None:
        game = self.games.get(game_id)
        if game is None or game.status != GameStatus.PENDING:
            return None
        if not game.has_player(player_id) or player_id == HOUSE_USER_ID:
            return None
        if player_id in game.ready_ids:
            return None
        game.ready_ids.append(player_id)
        game.version += 1
        return self._clone(game)

    async def list_completed(self, guild_id: str, limit: int = 10) -> list[GameRecord]:
        matches = [
            self._clone(game)
            for game in self.games.values()
            if game.guild_id == guild_id and game.status is GameStatus.COMPLETED
        ]
        matches.sort(key=lambda game: game.resolved_at or game.created_at or datetime.min, reverse=True)
        return matches[:limit]

    async def list_open(self, limit: int = 20) -> list[GameRecord]:
        matches = [
            self._clone(game)
            for game in self.games.values()
            if game.status is GameStatus.PENDING
        ]
        matches.sort(key=lambda game: game.created_at or datetime.min, reverse=True)
        return matches[:limit]

    async def list_completed_since(self, since: datetime | None = None) -> list[GameRecord]:
        matches = [
            self._clone(game)
            for game in self.games.values()
            if game.status is GameStatus.COMPLETED
            and (since is None or (game.resolved_at is not None and game.resolved_at >= since))
        ]
        matches.sort(key=lambda game: game.resolved_at or game.created_at or datetime.min, reverse=True)
        return matches

    async def replace(self, game: GameRecord, session=None) -> bool:
        current = self.games.get(game.game_id)
        if current is None or current.version != game.version:
            return False
        game.version += 1
        self.games[game.game_id] = game
        return True

    async def set_listing_ref(self, game_id: str, channel_id: str, message_id: str, session=None) -> None:
        game = self.games[game_id]
        game.channel_id = channel_id
        game.message_id = message_id

    async def set_private_ref(self, game_id: str, channel_id: str, message_id: str, session=None) -> None:
        game = self.games[game_id]
        game.private_channel_id = channel_id
        game.private_message_id = message_id

    async def set_table_number(self, game_id: str, table_number: int, session=None) -> None:
        self.games[game_id].table_number = table_number

    async def find_pending_in_channel(self, channel_id: str, session=None) -> GameRecord | None:
        for game in self.games.values():
            if game.private_channel_id == channel_id and game.status is GameStatus.PENDING:
                return self._clone(game)
        return None

    async def find_active_in_channel(self, channel_id: str, session=None) -> GameRecord | None:
        for game in self.games.values():
            if game.private_channel_id == channel_id and game.status is GameStatus.ACTIVE:
                return self._clone(game)
        return None

    async def list_due_channel_deletes(self, now: datetime, limit: int = 50) -> list[GameRecord]:
        matches = [
            self._clone(game)
            for game in self.games.values()
            if game.channel_delete_at is not None and game.channel_delete_at <= now
        ]
        return matches[:limit]

    async def clear_channel_delete_at(self, game_id: str, session=None) -> None:
        game = self.games.get(game_id)
        if game is not None:
            game.channel_delete_at = None

    async def set_guild_id(self, game_id: str, guild_id: str, session=None) -> None:
        self.games[game_id].guild_id = guild_id

    async def try_remove_player(self, game_id: str, player_id: str, session=None) -> GameRecord | None:
        game = self.games.get(game_id)
        if game is None or game.status != GameStatus.PENDING or not game.has_player(player_id):
            return None
        game.heads_ids = [seat for seat in game.heads_ids if seat != player_id]
        game.tails_ids = [seat for seat in game.tails_ids if seat != player_id]
        game.ready_ids = [seat for seat in game.ready_ids if seat != player_id]
        game.house_seat_ids = [seat for seat in game.house_seat_ids if seat != player_id]
        game.version += 1
        return self._clone(game)

    async def list_due_auto_flips(self, now: datetime, limit: int = 50) -> list[GameRecord]:
        matches = [
            self._clone(game)
            for game in self.games.values()
            if game.status is GameStatus.PENDING and game.auto_flip_at is not None and game.auto_flip_at <= now
        ]
        return matches[:limit]

    async def cancel_if_pending(self, game_id: str, status: GameStatus, now: datetime, session=None) -> GameRecord | None:
        game = self.games.get(game_id)
        if game is None or game.status != GameStatus.PENDING:
            return None
        game.status = status
        game.resolved_at = now
        game.version += 1
        return self._clone(game)

    def _clone(self, game: GameRecord) -> GameRecord:
        return GameRecord(
            game_id=game.game_id,
            status=game.status,
            creator_id=game.creator_id,
            bet_amount_lamports=game.bet_amount_lamports,
            currency=game.currency,
            bet_amount_fiat=game.bet_amount_fiat,
            bet_amount_usd=game.bet_amount_usd,
            player_wager_lamports=dict(game.player_wager_lamports),
            game_type=game.game_type,
            mode=game.mode,
            heads_ids=list(game.heads_ids),
            tails_ids=list(game.tails_ids),
            house_seat_ids=list(game.house_seat_ids),
            ready_ids=list(game.ready_ids),
            winner_id=game.winner_id,
            winner_ids=list(game.winner_ids),
            winner_side=game.winner_side,
            house_fee_lamports=game.house_fee_lamports,
            house_fee_usd=game.house_fee_usd,
            winner_payout_lamports=game.winner_payout_lamports,
            per_winner_payout_lamports=game.per_winner_payout_lamports,
            coin_result=game.coin_result,
            coin_flips=list(game.coin_flips),
            version=game.version,
            guild_id=game.guild_id,
            channel_id=game.channel_id,
            message_id=game.message_id,
            private_channel_id=game.private_channel_id,
            private_message_id=game.private_message_id,
            server_seed_hash=game.server_seed_hash,
            server_seed=game.server_seed,
            client_seed=game.client_seed,
            nonce=game.nonce,
            ledger_ids=list(game.ledger_ids),
            created_at=game.created_at,
            resolved_at=game.resolved_at,
            expires_at=game.expires_at,
            auto_flip_at=game.auto_flip_at,
            table_number=game.table_number,
            game_number=game.game_number,
            rematch_of_game_id=game.rematch_of_game_id,
            invited_ids=list(game.invited_ids),
            channel_delete_at=game.channel_delete_at,
            max_players=game.max_players,
            first_to=game.first_to,
            scores=dict(game.scores),
            turn_index=game.turn_index,
            round_index=game.round_index,
            roll_index=game.roll_index,
            current_round_rolls=list(game.current_round_rolls),
            dice_rolls=list(game.dice_rolls),
            dice_rolling_ids=list(game.dice_rolling_ids),
        )


class MemoryLedgerRepository:
    def __init__(self) -> None:
        self.entries: list[LedgerRecord] = []

    async def append(self, entry: LedgerRecord, session=None) -> None:
        self.entries.append(entry)

    async def list_for_user(self, user_id: str, limit: int = 25) -> list[LedgerRecord]:
        return [entry for entry in self.entries if entry.user_id == user_id][-limit:]

    async def list_for_game(self, game_id: str) -> list[LedgerRecord]:
        return [entry for entry in self.entries if entry.related_game_id == game_id]

    async def sum_signed_lamports(self, user_id: str, session=None) -> int:
        return sum(entry.amount_lamports for entry in self.entries if entry.user_id == user_id)


class MemoryTransactionRepository:
    def __init__(self) -> None:
        self.items: dict[str, TransactionRecord] = {}
        self.by_signature: dict[str, TransactionRecord] = {}

    async def insert(self, transaction: TransactionRecord, session=None) -> None:
        if transaction.solana_tx_signature and transaction.solana_tx_signature in self.by_signature:
            raise AlreadyProcessedError()
        self.items[transaction.transaction_id] = transaction
        if transaction.solana_tx_signature:
            self.by_signature[transaction.solana_tx_signature] = transaction

    async def find_by_signature(self, signature: str, session=None) -> TransactionRecord | None:
        return self.by_signature.get(signature)

    async def find_by_id(self, transaction_id: str, session=None) -> TransactionRecord | None:
        return self.items.get(transaction_id)

    async def attach_signature(self, transaction_id: str, signature: str, session=None) -> None:
        transaction = self.items.get(transaction_id)
        if transaction is None:
            return
        transaction.solana_tx_signature = signature
        self.by_signature[signature] = transaction


class MemoryRainRepository:
    def __init__(self) -> None:
        self.items: dict[str, RainRecord] = {}

    async def insert(self, rain: RainRecord, session=None) -> None:
        self.items[rain.rain_id] = rain

    async def find_by_id(self, rain_id: str, session=None) -> RainRecord | None:
        rain = self.items.get(rain_id)
        return self._clone(rain) if rain else None

    async def replace(self, rain: RainRecord, session=None) -> bool:
        current = self.items.get(rain.rain_id)
        if current is None or current.version != rain.version:
            return False
        rain.version += 1
        self.items[rain.rain_id] = rain
        return True

    async def set_listing_ref(self, rain_id: str, channel_id: str, message_id: str, session=None) -> None:
        rain = self.items[rain_id]
        rain.channel_id = channel_id
        rain.message_id = message_id

    async def try_add_joiner(self, rain_id: str, player_id: str, now, session=None) -> RainRecord | None:
        rain = self.items.get(rain_id)
        if rain is None or rain.status is not RainStatus.PENDING:
            return None
        if rain.creator_id == player_id or player_id in rain.joiner_ids:
            return None
        if rain.expires_at is not None and rain.expires_at <= now:
            return None
        rain.joiner_ids.append(player_id)
        rain.version += 1
        return self._clone(rain)

    async def try_begin_settle(self, rain_id: str, now, session=None) -> RainRecord | None:
        rain = self.items.get(rain_id)
        if rain is None or rain.status is not RainStatus.PENDING:
            return None
        if rain.expires_at is not None and rain.expires_at > now:
            return None
        rain.status = RainStatus.SETTLING
        rain.version += 1
        return self._clone(rain)

    async def list_due(self, now, limit: int = 50, session=None) -> list[RainRecord]:
        matches = [
            self._clone(rain)
            for rain in self.items.values()
            if rain.status in {RainStatus.PENDING, RainStatus.SETTLING}
            and rain.expires_at is not None
            and rain.expires_at <= now
        ]
        return matches[:limit]

    async def list_open(self, session=None) -> list[RainRecord]:
        return [
            self._clone(rain)
            for rain in self.items.values()
            if rain.status is RainStatus.PENDING
        ]

    def _clone(self, rain: RainRecord | None) -> RainRecord | None:
        if rain is None:
            return None
        return replace(
            rain,
            joiner_ids=list(rain.joiner_ids),
            ledger_ids=list(rain.ledger_ids),
            payouts_lamports=dict(rain.payouts_lamports),
        )


class MemoryHouseRepository:
    def __init__(self) -> None:
        self.record = HouseTreasuryRecord()
        self.next_fair_nonce = 0
        self.next_game_numbers: dict[str, int] = {}

    async def get(self, session=None) -> HouseTreasuryRecord:
        return self.record

    async def credit_fee(self, amount: SolAmount, now: datetime, session=None) -> HouseTreasuryRecord:
        self.record.sol_balance_lamports += amount.lamports
        self.record.total_fees_collected_lamports += amount.lamports
        self.record.version += 1
        self.record.updated_at = now
        return self.record

    async def credit_reserve(self, amount: SolAmount, now: datetime, session=None) -> HouseTreasuryRecord:
        self.record.sol_balance_lamports += amount.lamports
        self.record.version += 1
        self.record.updated_at = now
        return self.record

    async def debit_reserve(self, amount: SolAmount, now: datetime, session=None) -> HouseTreasuryRecord:
        if self.record.sol_balance_lamports < amount.lamports:
            raise InsufficientHouseReserveError()
        self.record.sol_balance_lamports -= amount.lamports
        self.record.version += 1
        self.record.updated_at = now
        return self.record

    async def take_next_fair_nonce(self, session=None) -> int:
        self.next_fair_nonce += 1
        return self.next_fair_nonce

    async def take_next_game_number(self, game_type, session=None) -> int:
        key = game_type.value
        self.next_game_numbers[key] = self.next_game_numbers.get(key, 0) + 1
        return self.next_game_numbers[key]


class NullUnitOfWork:
    session = None

    async def __aenter__(self) -> NullUnitOfWork:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class NullUnitOfWorkFactory:
    def begin(self) -> NullUnitOfWork:
        return NullUnitOfWork()
