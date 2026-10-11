from __future__ import annotations

import hmac
from hashlib import sha256

from solders.keypair import Keypair


class DepositWalletDeriver:
    """Derive a stable per-player Solana deposit keypair from one master secret."""

    def __init__(self, master_secret: str) -> None:
        self._master = master_secret.encode("utf-8")

    def keypair_for(self, discord_id: str) -> Keypair:
        seed = hmac.new(self._master, discord_id.encode("utf-8"), sha256).digest()
        return Keypair.from_seed(seed)

    def address_for(self, discord_id: str) -> str:
        return str(self.keypair_for(discord_id).pubkey())
