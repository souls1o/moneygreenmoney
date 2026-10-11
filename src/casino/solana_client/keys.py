from __future__ import annotations

import json

from solders.keypair import Keypair

from casino.errors import ConfigurationError

_ENV_PREFIXES = (
    "SOLANA_PRIVATE_KEY=",
    "SUPER_WALLET_PRIVATE_KEY=",
    "DEPOSIT_MASTER_SECRET=",
    "HOUSE_WALLET_PRIVATE_KEY=",
)


def normalize_secret(secret: str) -> str:
    raw = secret.strip().strip("'").strip('"').strip()
    changed = True
    while changed and raw:
        changed = False
        upper = raw.upper()
        for prefix in _ENV_PREFIXES:
            if upper.startswith(prefix):
                raw = raw[len(prefix) :].strip().strip("'").strip('"').strip()
                changed = True
                break
    return raw


class KeypairLoader:
    """Load a Solana keypair from env without logging secret material."""

    def load(self, secret: str) -> Keypair:
        raw = normalize_secret(secret)
        if not raw:
            raise ConfigurationError("Wallet secret is empty.")
        try:
            if raw.startswith("["):
                values = json.loads(raw)
                return Keypair.from_bytes(bytes(values))
            return Keypair.from_base58_string(raw)
        except Exception as exc:
            raise ConfigurationError("Wallet secret could not be parsed.") from exc
