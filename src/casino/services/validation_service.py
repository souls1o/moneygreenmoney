from __future__ import annotations

from decimal import Decimal

from casino.domain.enums import CoinSide
from casino.errors import ValidationError
from casino.utils.money import MathService


class ValidationService:
    def __init__(
        self,
        math_service: MathService,
        min_bet_usd: Decimal,
        supported_currencies: tuple[str, ...],
    ) -> None:
        self._math = math_service
        self._min_bet_usd = min_bet_usd
        self._supported_currencies = supported_currencies

    def parse_fiat_amount(self, raw: str) -> Decimal:
        return self._math.parse_positive_decimal(raw)

    def require_supported_currency(self, currency: str) -> str:
        code = currency.strip().upper()
        if code not in self._supported_currencies:
            allowed = ", ".join(self._supported_currencies)
            raise ValidationError(f"Supported currencies: {allowed}.")
        return code

    def parse_coin_side(self, raw: str) -> CoinSide:
        normalized = raw.strip().lower()
        try:
            return CoinSide(normalized)
        except ValueError as exc:
            raise ValidationError("Choose heads or tails.") from exc

    def parse_duration_seconds(self, raw: str) -> int:
        text = raw.strip().lower()
        if not text:
            raise ValidationError("Enter a duration such as 30s, 5m, 2h, or 1d.")
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86_400}
        unit = text[-1]
        number = text[:-1] if unit in multipliers else text
        scale = multipliers.get(unit, 1)
        try:
            value = Decimal(number)
        except Exception as exc:
            raise ValidationError("Enter a duration such as 30s, 5m, 2h, or 1d.") from exc
        seconds = int(value * scale)
        if seconds < 1:
            raise ValidationError("Duration must be at least 1 second.")
        return seconds

    def validate_bet(self, amount_usd: Decimal) -> None:
        if amount_usd < self._min_bet_usd:
            raise ValidationError(
                f"Minimum bet is {self._math.format_fiat_symbol(self._min_bet_usd, 'USD')}."
            )
