from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from decimal import Decimal

from casino.domain.blackjack import BLACKJACK_SHOE_SIZE, BlackjackCard, card_from_index
from casino.domain.enums import CoinSide
from casino.domain.limbo import crash_from_digest

_ROLL_DENOMINATOR = Decimal(0x1_0000_0000)
_ROLL_RANGE = Decimal(100)


@dataclass(frozen=True, slots=True)
class FairCommitment:
    server_seed: str
    server_seed_hash: str


@dataclass(frozen=True, slots=True)
class FairPlaySeeds:
    server_seed: str
    server_seed_hash: str
    client_seed: str
    nonce: int
    next_server_seed_hash: str


@dataclass(frozen=True, slots=True)
class FairCard:
    digest: str
    card: BlackjackCard
    card_index: int


@dataclass(frozen=True, slots=True)
class FairDice:
    digest: str
    die_a: int
    die_b: int
    roll_index: int

    @property
    def total(self) -> int:
        return self.die_a + self.die_b


@dataclass(frozen=True, slots=True)
class FairJackpot:
    digest: str
    ticket: int
    winner_id: str


@dataclass(frozen=True, slots=True)
class FairRainSplit:
    digest: str
    shares: dict[str, int]


@dataclass(frozen=True, slots=True)
class FairLimbo:
    digest: str
    crash: Decimal


@dataclass(frozen=True, slots=True)
class FairMines:
    digest: str
    mines: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FairTarget:
    digest: str
    value: int
    index: int


@dataclass(frozen=True, slots=True)
class FairRoll:
    digest: str
    roll: Decimal
    side: CoinSide


class FairRandomService:
    """Cryptographically secure coin flips. Never uses Math.random / random.random."""

    def flip_coin(self) -> CoinSide:
        return CoinSide.HEADS if secrets.randbits(1) == 0 else CoinSide.TAILS


class ProvablyFairService:
    """Commit-reveal RNG. Result is HMAC(server_seed, client_seed:nonce)."""

    def commit(self) -> FairCommitment:
        server_seed = secrets.token_hex(32)
        return FairCommitment(
            server_seed=server_seed,
            server_seed_hash=self.hash_server_seed(server_seed),
        )

    def client_seed(self) -> str:
        return secrets.token_hex(16)

    def nonce(self) -> int:
        return secrets.randbits(32)

    def hash_server_seed(self, server_seed: str) -> str:
        return hashlib.sha256(server_seed.encode("utf-8")).hexdigest()

    def resolve_coin(
        self,
        server_seed: str,
        client_seed: str,
        nonce: int,
        flip_index: int = 0,
    ) -> CoinSide:
        return self.resolve_roll(server_seed, client_seed, nonce, flip_index).side

    def resolve_roll(
        self,
        server_seed: str,
        client_seed: str,
        nonce: int,
        flip_index: int = 0,
    ) -> FairRoll:
        digest = hmac.new(
            server_seed.encode("utf-8"),
            f"{client_seed}:{nonce + flip_index}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        raw = int(digest[:8], 16)
        roll = (Decimal(raw) / _ROLL_DENOMINATOR) * _ROLL_RANGE
        return FairRoll(
            digest=digest,
            roll=roll,
            side=CoinSide.HEADS if roll < 50 else CoinSide.TAILS,
        )

    def resolve_card(self, server_seed: str, client_seed: str, nonce: int, card_index: int) -> FairCard:
        remaining = [index % 52 for index in range(BLACKJACK_SHOE_SIZE)]
        digest = ""
        chosen = 0
        draws = min(max(int(card_index), 0) + 1, len(remaining))
        for draw in range(draws):
            digest = hmac.new(
                server_seed.encode("utf-8"),
                f"{client_seed}:{nonce}:shoe:{draw}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            pick = int(digest[:8], 16) % len(remaining)
            chosen = remaining.pop(pick)
        return FairCard(digest=digest, card=card_from_index(chosen), card_index=card_index)

    def resolve_target(self, server_seed: str, client_seed: str, nonce: int, index: int = 0) -> FairTarget:
        digest = hmac.new(
            server_seed.encode("utf-8"),
            f"{client_seed}:{nonce}:target:{index}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        raw = int(digest[:8], 16)
        return FairTarget(digest=digest, value=raw % 999 + 1, index=index)

    def resolve_mines(
        self,
        server_seed: str,
        client_seed: str,
        nonce: int,
        mine_count: int,
        *,
        tiles: int = 25,
    ) -> FairMines:
        remaining = list(range(int(tiles)))
        mines: list[int] = []
        digest = ""
        draws = min(max(int(mine_count), 0), len(remaining))
        for draw in range(draws):
            digest = hmac.new(
                server_seed.encode("utf-8"),
                f"{client_seed}:{nonce}:mines:{draw}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            pick = int(digest[:8], 16) % len(remaining)
            mines.append(remaining.pop(pick))
        return FairMines(digest=digest, mines=tuple(sorted(mines)))

    def resolve_limbo(self, server_seed: str, client_seed: str, nonce: int) -> FairLimbo:
        digest = hmac.new(
            server_seed.encode("utf-8"),
            f"{client_seed}:{nonce}:limbo".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return FairLimbo(digest=digest, crash=crash_from_digest(digest))

    def resolve_dice(self, server_seed: str, client_seed: str, nonce: int, roll_index: int) -> FairDice:
        digest = hmac.new(
            server_seed.encode("utf-8"),
            f"{client_seed}:{nonce}:{roll_index}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        die_a = int(digest[0:8], 16) % 6 + 1
        die_b = int(digest[8:16], 16) % 6 + 1
        return FairDice(
            digest=digest,
            die_a=die_a,
            die_b=die_b,
            roll_index=roll_index,
        )

    def resolve_jackpot(
        self,
        server_seed: str,
        client_seed: str,
        nonce: int,
        entries: dict[str, int],
    ) -> FairJackpot:
        from casino.domain.jackpot import pick_weighted_winner

        digest = hmac.new(
            server_seed.encode("utf-8"),
            f"{client_seed}:{nonce}:jackpot".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        total = sum(max(int(value), 0) for value in entries.values())
        ticket = int(digest[:16], 16) % total if total else 0
        winner_id = pick_weighted_winner(entries, ticket) if total else ""
        return FairJackpot(digest=digest, ticket=ticket, winner_id=winner_id)

    def resolve_rain(
        self,
        server_seed: str,
        client_seed: str,
        nonce: int,
        joiner_ids: list[str],
        amount_lamports: int,
    ) -> FairRainSplit:
        digest = hmac.new(
            server_seed.encode("utf-8"),
            f"{client_seed}:{nonce}:rain".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not joiner_ids:
            return FairRainSplit(digest=digest, shares={})
        base = amount_lamports // len(joiner_ids)
        extra = amount_lamports % len(joiner_ids)
        shares = {
            player_id: base + (1 if index < extra else 0)
            for index, player_id in enumerate(joiner_ids)
        }
        return FairRainSplit(digest=digest, shares=shares)

    def verify(
        self,
        *,
        server_seed: str,
        server_seed_hash: str,
        client_seed: str,
        nonce: int,
        expected: CoinSide,
    ) -> bool:
        if self.hash_server_seed(server_seed) != server_seed_hash:
            return False
        return self.resolve_coin(server_seed, client_seed, nonce) is expected
