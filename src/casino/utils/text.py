from __future__ import annotations

from casino.errors import ValidationError


class TextNormalizer:
    def discord_id(self, value: str | int) -> str:
        text = str(value).strip()
        if not text.isdigit():
            raise ValidationError("Discord IDs must be numeric.")
        return text

    def currency_code(self, value: str) -> str:
        code = value.strip().upper()
        if not code.isalpha() or not (3 <= len(code) <= 4):
            raise ValidationError("Currency codes must be 3–4 letters.")
        return code

    def signature(self, value: str) -> str:
        text = value.strip()
        if len(text) < 32 or len(text) > 128:
            raise ValidationError("That Solana signature does not look valid.")
        return text
