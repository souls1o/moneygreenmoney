from __future__ import annotations

import secrets
import uuid

from casino.domain.enums import GameType


class IdentifierFactory:
    """Reusable identity generation for games, ledger rows, and deposit memos."""

    GAME_PREFIXES = {
        GameType.COINFLIP: "cf",
        GameType.DICE: "dc",
        GameType.BLACKJACK: "bj",
        GameType.TARGET: "tg",
        GameType.LIMBO: "lb",
        GameType.MINES: "mn",
        GameType.JACKPOT: "jp",
        GameType.BACCARAT: "bc",
    }
    LEDGER_PREFIX = "ld"
    TX_PREFIX = "tx"
    MEMO_PREFIX = "BB"

    def game_id(self, game_type: GameType | None = None) -> str:
        prefix = self.GAME_PREFIXES.get(game_type, "gm") if game_type is not None else "gm"
        return f"{prefix}_{uuid.uuid4().hex}"

    def ledger_id(self) -> str:
        return f"{self.LEDGER_PREFIX}_{uuid.uuid4().hex}"

    def transaction_id(self) -> str:
        return f"{self.TX_PREFIX}_{uuid.uuid4().hex}"

    def deposit_memo(self) -> str:
        return f"{self.MEMO_PREFIX}{secrets.token_hex(4).upper()}"

    def lock_owner(self) -> str:
        return uuid.uuid4().hex

    def rain_id(self) -> str:
        return f"rn_{uuid.uuid4().hex}"

    def ticket_id(self) -> str:
        return f"tk_{uuid.uuid4().hex}"

    def jackpot_id(self) -> str:
        return f"jp_{uuid.uuid4().hex}"

    def baccarat_id(self) -> str:
        return f"bc_{uuid.uuid4().hex}"

    def wager_race_id(self) -> str:
        return f"wr_{uuid.uuid4().hex}"
