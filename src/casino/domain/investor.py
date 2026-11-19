from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


def total_shares(founder_shares: int, investor_shares: int) -> int:
    return max(0, founder_shares) + max(0, investor_shares)


def equity_lamports(shares: int, book_lamports: int, pool_shares: int) -> int:
    if shares <= 0 or pool_shares <= 0 or book_lamports <= 0:
        return 0
    return (shares * book_lamports) // pool_shares


def shares_to_mint(deposit_lamports: int, book_before: int, pool_shares: int) -> int:
    if deposit_lamports <= 0:
        return 0
    if pool_shares <= 0:
        return deposit_lamports
    if book_before <= 0:
        return 0
    return (deposit_lamports * pool_shares) // book_before


def shares_to_burn(amount_lamports: int, book_lamports: int, pool_shares: int, held_shares: int) -> int | None:
    equity = equity_lamports(held_shares, book_lamports, pool_shares)
    if amount_lamports <= 0 or equity <= 0 or amount_lamports > equity:
        return None
    if amount_lamports >= equity:
        return held_shares
    burn = (amount_lamports * pool_shares) // book_lamports
    return burn if burn > 0 else None


def redeem(amount_lamports: int, book_lamports: int, pool_shares: int, user_shares: int) -> tuple[int, int]:
    """Return `(shares_burned, lamports_paid)` at current NAV, capped to equity and book."""
    equity = equity_lamports(user_shares, book_lamports, pool_shares)
    if amount_lamports <= 0 or equity <= 0 or book_lamports <= 0 or pool_shares <= 0:
        return 0, 0
    pay = min(amount_lamports, equity, book_lamports)
    if pay >= equity:
        return user_shares, min(equity, book_lamports)
    burn = (pay * pool_shares) // book_lamports
    if burn <= 0:
        return 0, 0
    if burn >= user_shares:
        return user_shares, min(equity, book_lamports)
    return burn, (burn * book_lamports) // pool_shares


def ownership_bps(shares: int, pool_shares: int) -> int:
    if shares <= 0 or pool_shares <= 0:
        return 0
    return (shares * 10_000) // pool_shares


def forecast_ev_lamports(human_handle_lamports: int, house_fee_bps: int) -> int:
    if human_handle_lamports <= 0 or house_fee_bps <= 0:
        return 0
    return (human_handle_lamports * house_fee_bps) // 10_000


@dataclass(frozen=True, slots=True)
class InvestorPosition:
    book_lamports: int
    founder_shares: int
    investor_shares: int
    user_shares: int
    user_equity_lamports: int
    founder_equity_lamports: int
    investor_pool_lamports: int
    ownership_bps: int
    human_handle_lamports: int
    forecast_ev_lamports: int
    user_forecast_ev_lamports: int
    house_fee_bps: int
    player_balance_lamports: int
    min_invest_usd: Decimal
    max_invest_usd: Decimal

    def pool_shares(self) -> int:
        return total_shares(self.founder_shares, self.investor_shares)
