from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

AFFILIATE_BASE_BPS = 500
AFFILIATE_TIERS: tuple[tuple[Decimal, int], ...] = (
    (Decimal("0"), 500),
    (Decimal("500"), 600),
    (Decimal("1000"), 700),
    (Decimal("2500"), 800),
    (Decimal("5000"), 900),
    (Decimal("10000"), 1000),
)


def affiliate_rate_bps(wagered_usd: Decimal) -> int:
    """Commission on collected house fee from post-bind referred wager volume."""
    rate = AFFILIATE_BASE_BPS
    amount = wagered_usd if wagered_usd > 0 else Decimal("0")
    for threshold, bps in AFFILIATE_TIERS:
        if amount >= threshold:
            rate = bps
    return rate


def next_affiliate_tier(wagered_usd: Decimal) -> tuple[Decimal, int] | None:
    amount = wagered_usd if wagered_usd > 0 else Decimal("0")
    for threshold, bps in AFFILIATE_TIERS:
        if amount < threshold:
            return threshold, bps
    return None


def affiliate_cut_lamports(collected_fee_lamports: int, rate_bps: int, human_count: int) -> int:
    if collected_fee_lamports <= 0 or rate_bps < 1 or human_count < 1:
        return 0
    return (collected_fee_lamports * rate_bps) // (10_000 * human_count)


def format_affiliate_rate(rate_bps: int) -> str:
    whole, frac = divmod(rate_bps, 100)
    if frac == 0:
        return f"{whole}%"
    return f"{rate_bps / 100:.2f}%"


@dataclass(frozen=True, slots=True)
class AffiliateReferral:
    discord_id: str
    referred_wagered_usd: Decimal
    rate_bps: int
    referred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AffiliateStats:
    referred_by: str | None
    referred_at: datetime | None
    own_referred_wagered_usd: Decimal
    own_rate_bps: int
    referral_count: int
    earned_lamports: int
    earned_usd: Decimal
    referrals: list[AffiliateReferral]
