from __future__ import annotations

from enum import StrEnum


class GameType(StrEnum):
    COINFLIP = "coinflip"
    DICE = "dice"
    BLACKJACK = "blackjack"
    TARGET = "target"
    LIMBO = "limbo"
    MINES = "mines"
    JACKPOT = "jackpot"


class GameMode(StrEnum):
    SOLO = "solo"
    TEAM = "team"

    def seats_per_side(self) -> int:
        return 2 if self is GameMode.TEAM else 1


class GameStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class CoinSide(StrEnum):
    HEADS = "heads"
    TAILS = "tails"

    def opposite(self) -> CoinSide:
        return CoinSide.TAILS if self is CoinSide.HEADS else CoinSide.HEADS


class TransactionType(StrEnum):
    DEPOSIT = "deposit"
    BET = "bet"
    WIN = "win"
    FEE = "fee"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"
    WITHDRAWAL = "withdrawal"
    RAIN = "rain"
    RAIN_PAYOUT = "rain_payout"
    TIP = "tip"
    AFFILIATE = "affiliate"
    INVEST = "invest"
    DIVEST = "divest"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class LedgerEntryType(StrEnum):
    DEPOSIT = "deposit"
    BET = "bet"
    WIN = "win"
    FEE = "fee"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"
    WITHDRAWAL = "withdrawal"
    RAIN = "rain"
    RAIN_PAYOUT = "rain_payout"
    TIP = "tip"
    AFFILIATE = "affiliate"
    INVEST = "invest"
    DIVEST = "divest"


class UserRole(StrEnum):
    PLAYER = "player"
    MODERATOR = "moderator"
    FOUNDER = "founder"


class GuildConfigSlot(StrEnum):
    OPEN_GAMES = "open_games"
    OPEN_COINFLIP = "open_coinflip"
    OPEN_DICE = "open_dice"
    OPEN_BLACKJACK = "open_blackjack"
    OPEN_TARGET = "open_target"
    COINFLIP_OUTCOMES = "coinflip_outcomes"
    COINFLIP_CATEGORY = "coinflip_category"
    DICE_OUTCOMES = "dice_outcomes"
    DICE_CATEGORY = "dice_category"
    BLACKJACK_OUTCOMES = "blackjack_outcomes"
    TARGET_OUTCOMES = "target_outcomes"
    PROVABLY_FAIR = "provably_fair"
    DEPOSIT_ALERTS = "deposits"
    WITHDRAWAL_ALERTS = "withdrawals"
    HOUSE_LEDGER = "house_ledger"
    RAIN = "rain"
    RAIN_PING = "rain_ping"
    VERIFY = "verify"
    MEMBER_ROLE = "member_role"
    INVESTOR_ROLE = "investor_role"
    SUPPORT = "support"
    SUPPORT_CATEGORY = "support_category"
    SUPPORT_ROLE = "support_role"
    SUPPORT_LOGS = "support_logs"
    JACKPOT = "jackpot"
    JACKPOT_HISTORY = "jackpot_history"

    def is_category(self) -> bool:
        return self in {
            GuildConfigSlot.COINFLIP_CATEGORY,
            GuildConfigSlot.DICE_CATEGORY,
            GuildConfigSlot.SUPPORT_CATEGORY,
        }

    @classmethod
    def outcomes_for(cls, game_type: GameType) -> GuildConfigSlot:
        if game_type is GameType.DICE:
            return cls.DICE_OUTCOMES
        if game_type is GameType.BLACKJACK or game_type is GameType.LIMBO or game_type is GameType.MINES:
            return cls.BLACKJACK_OUTCOMES
        if game_type is GameType.TARGET:
            return cls.TARGET_OUTCOMES
        if game_type is GameType.JACKPOT:
            return cls.JACKPOT_HISTORY
        return cls.COINFLIP_OUTCOMES

    @classmethod
    def category_for(cls, game_type: GameType) -> GuildConfigSlot:
        if game_type is GameType.DICE:
            return cls.DICE_CATEGORY
        return cls.COINFLIP_CATEGORY


class JackpotStatus(StrEnum):
    OPEN = "open"
    SETTLING = "settling"
    SHOWING = "showing"
    COMPLETED = "completed"
    REFUNDED = "refunded"


class RainStatus(StrEnum):
    PENDING = "pending"
    SETTLING = "settling"
    COMPLETED = "completed"
    REFUNDED = "refunded"


class TicketStatus(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    CLOSED = "closed"


class TicketCategory(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    GAME = "game"
    ACCOUNT = "account"
    OTHER = "other"

    def label(self) -> str:
        return {
            TicketCategory.DEPOSIT: "Deposits",
            TicketCategory.WITHDRAWAL: "Withdrawals",
            TicketCategory.GAME: "Games",
            TicketCategory.ACCOUNT: "Account",
            TicketCategory.OTHER: "Other",
        }[self]
