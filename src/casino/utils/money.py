from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from casino.domain.currencies import CurrencyCatalog
from casino.errors import ValidationError

LAMPORTS_PER_SOL = 1_000_000_000
SOL_QUANTUM = Decimal("0.000000001")
FIAT_QUANTUM = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.00000001")
TRANSFER_FEE_BPS = 100


@dataclass(frozen=True, slots=True, order=True)
class SolAmount:
    """Exact SOL value stored as integer lamports. Never use floats for money."""

    lamports: int

    def __post_init__(self) -> None:
        if self.lamports < 0:
            raise ValidationError("SOL amounts cannot be negative.")

    @classmethod
    def zero(cls) -> SolAmount:
        return cls(0)

    @classmethod
    def from_lamports(cls, lamports: int) -> SolAmount:
        return cls(int(lamports))

    @classmethod
    def from_decimal(cls, value: Decimal) -> SolAmount:
        if value < 0:
            raise ValidationError("SOL amounts cannot be negative.")
        quantized = value.quantize(SOL_QUANTUM, rounding=ROUND_DOWN)
        return cls(int(quantized * LAMPORTS_PER_SOL))

    @classmethod
    def parse(cls, raw: str | Decimal | int) -> SolAmount:
        if isinstance(raw, int):
            return cls.from_lamports(raw)
        if isinstance(raw, Decimal):
            return cls.from_decimal(raw)
        try:
            parsed = Decimal(raw.strip())
        except (InvalidOperation, AttributeError) as exc:
            raise ValidationError("Enter a valid SOL amount.") from exc
        return cls.from_decimal(parsed)

    def to_decimal(self) -> Decimal:
        return (Decimal(self.lamports) / Decimal(LAMPORTS_PER_SOL)).quantize(SOL_QUANTUM)

    def __add__(self, other: SolAmount) -> SolAmount:
        return SolAmount(self.lamports + other.lamports)

    def __sub__(self, other: SolAmount) -> SolAmount:
        result = self.lamports - other.lamports
        if result < 0:
            raise ValidationError("Resulting SOL amount cannot be negative.")
        return SolAmount(result)

    def __mul__(self, scalar: int) -> SolAmount:
        if scalar < 0:
            raise ValidationError("Cannot scale a SOL amount by a negative integer.")
        return SolAmount(self.lamports * scalar)

    def is_zero(self) -> bool:
        return self.lamports == 0


def transfer_fee(amount: SolAmount) -> SolAmount:
    return SolAmount((amount.lamports * TRANSFER_FEE_BPS) // 10_000)


def subtract_transfer_fee(amount: SolAmount) -> SolAmount:
    return SolAmount(amount.lamports - transfer_fee(amount).lamports)


@dataclass(frozen=True, slots=True)
class PotSplit:
    total_pot: SolAmount
    house_fee: SolAmount
    winner_payout: SolAmount


class MathService:
    """Pure, reusable money arithmetic. House fee is always bps of the total pot."""

    def __init__(self, house_fee_bps: int) -> None:
        if house_fee_bps < 0 or house_fee_bps > 10_000:
            raise ValidationError("House fee basis points must be between 0 and 10000.")
        self._house_fee_bps = house_fee_bps

    @property
    def house_fee_bps(self) -> int:
        return self._house_fee_bps

    def split_equal_pot(self, bet_amount: SolAmount) -> PotSplit:
        return self.split_pot(bet_amount + bet_amount)

    def split_matched_pot(self, seat_bet: SolAmount, seats_per_side: int) -> PotSplit:
        if seats_per_side < 1:
            raise ValidationError("Each side must have at least one seat.")
        return self.split_player_pot(seat_bet, seats_per_side * 2)

    def split_player_pot(self, seat_bet: SolAmount, player_count: int) -> PotSplit:
        if player_count < 2:
            raise ValidationError("A pot needs at least two players.")
        return self.split_pot(seat_bet * player_count)

    def split_evenly(self, amount: SolAmount, parts: int) -> list[SolAmount]:
        if parts < 1:
            raise ValidationError("Cannot split a pot among zero winners.")
        base = amount.lamports // parts
        remainder = amount.lamports % parts
        return [SolAmount(base + (1 if index < remainder else 0)) for index in range(parts)]

    def split_pot(self, total_pot: SolAmount) -> PotSplit:
        fee_lamports = (total_pot.lamports * self._house_fee_bps) // 10_000
        fee = SolAmount(fee_lamports)
        return PotSplit(
            total_pot=total_pot,
            house_fee=fee,
            winner_payout=total_pot - fee,
        )

    def collected_house_fee(
        self,
        pot_fee: SolAmount,
        *,
        human_winner_count: int,
        winner_count: int,
    ) -> SolAmount:
        if winner_count < 1 or human_winner_count < 1:
            return SolAmount.zero()
        return SolAmount((pot_fee.lamports * human_winner_count) // winner_count)

    def fiat_to_sol(self, fiat_amount: Decimal, sol_price: Decimal) -> SolAmount:
        if fiat_amount <= 0:
            raise ValidationError("Bet amount must be greater than zero.")
        if sol_price <= 0:
            raise ValidationError("SOL price must be greater than zero.")
        return SolAmount.from_decimal(fiat_amount / sol_price)

    def sol_to_fiat(self, amount: SolAmount, sol_price: Decimal) -> Decimal:
        if sol_price <= 0:
            raise ValidationError("SOL price must be greater than zero.")
        return (amount.to_decimal() * sol_price).quantize(PRICE_QUANTUM)

    def format_sol(self, amount: SolAmount) -> str:
        text = format(amount.to_decimal(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    def format_fiat(self, amount: Decimal, currency: str) -> str:
        quantized = amount.quantize(FIAT_QUANTUM, rounding=ROUND_DOWN)
        return f"{quantized:,.2f} {currency}"

    def format_fiat_symbol(self, amount: Decimal, currency: str) -> str:
        quantized = amount.quantize(FIAT_QUANTUM, rounding=ROUND_DOWN)
        return f"{CurrencyCatalog.symbol(currency)}{quantized:,.2f}"

    def parse_positive_decimal(self, raw: str) -> Decimal:
        try:
            value = Decimal(raw.strip())
        except (InvalidOperation, AttributeError) as exc:
            raise ValidationError("Enter a valid numeric amount.") from exc
        if value <= 0:
            raise ValidationError("Amount must be greater than zero.")
        return value
