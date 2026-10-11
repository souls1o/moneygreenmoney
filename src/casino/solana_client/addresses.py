from __future__ import annotations

from solders.pubkey import Pubkey

from casino.errors import ValidationError


class SolanaAddressValidator:
    def parse(self, raw: str) -> str:
        text = raw.strip()
        try:
            pubkey = Pubkey.from_string(text)
        except ValueError as exc:
            raise ValidationError("That is not a valid Solana address.") from exc
        if pubkey == Pubkey.default():
            raise ValidationError("That is not a valid Solana address.")
        return str(pubkey)

    def to_pubkey(self, raw: str) -> Pubkey:
        return Pubkey.from_string(self.parse(raw))
