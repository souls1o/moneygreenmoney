from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from decimal import Decimal

from casino.domain.enums import CoinSide

_ROLL_DENOMINATOR = Decimal(0x1_0000_0000)
_ROLL_RANGE = Decimal(100)


@dataclass(frozen=True, slots=True)
class FairCommitment:
    server_seed: str
    server_seed_hash: str


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
class FairRainSplit:
    digest: str
    shares: dict[str, int]


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
