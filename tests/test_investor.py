from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.enums import LedgerEntryType, TransactionType
from casino.domain.investor import equity_lamports, redeem, shares_to_burn, shares_to_mint
from casino.domain.records import UserRecord
from casino.errors import InvestorError, WithdrawalError
from casino.services.admin_service import AdminService
from casino.services.balance_service import BalanceService
from casino.services.investor_service import InvestorService
from casino.services.ledger_service import LedgerService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.solana_client.addresses import SolanaAddressValidator
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService, SolAmount
from tests.conftest import FrozenClock, StubPriceService, StubSettings
from tests.fakes import (
    MemoryHouseRepository,
    MemoryLedgerRepository,
    MemoryTransactionRepository,
    MemoryUserRepository,
    NullUnitOfWorkFactory,
)
from tests.test_withdrawal_service import DESTINATION, StubRpc


def test_share_math_nine_plus_one() -> None:
    book = 9_000_000_000
    pool = 9_000_000_000
    minted = shares_to_mint(1_000_000_000, book, pool)
    assert minted == 1_000_000_000
    new_book = 10_000_000_000
    new_pool = 10_000_000_000
    assert equity_lamports(minted, new_book, new_pool) == 1_000_000_000
    after_fee = 10_200_000_000
    assert equity_lamports(minted, after_fee, new_pool) == 1_020_000_000
    after_loss = 9_200_000_000
    assert equity_lamports(minted, after_loss, new_pool) == 920_000_000
    burned, paid = redeem(1_020_000_000, after_fee, new_pool, minted)
    assert burned == minted
    assert paid == 1_020_000_000
    assert shares_to_burn(9_000_000_001, 10_000_000_000, new_pool, 9_000_000_000) is None
    assert shares_to_burn(9_000_000_000, 10_000_000_000, new_pool, 9_000_000_000) == 9_000_000_000


class InvestorHarness:
    def __init__(self, house_sol: str = "9") -> None:
        self.clock = FrozenClock()
        self.ids = IdentifierFactory()
        self.math = MathService(300)
        self.users_repo = MemoryUserRepository()
        self.ledger_repo = MemoryLedgerRepository()
        self.tx_repo = MemoryTransactionRepository()
        self.house_repo = MemoryHouseRepository()
        self.house_repo.record.sol_balance_lamports = SolAmount.parse(house_sol).lamports
        ledger = LedgerService(self.ledger_repo, self.ids, self.clock)
        self.balances = BalanceService(
            self.users_repo, self.tx_repo, self.house_repo, ledger, self.ids, self.clock
        )
        settings = StubSettings()
        self.users = UserService(self.users_repo, self.ids, self.clock, settings)  # type: ignore[arg-type]
        self.investors = InvestorService(
            self.users,
            self.users_repo,
            self.balances,
            self.house_repo,
            StubPriceService(),
            ValidationService(
                self.math,
                Decimal("0.01"),
                ("USD", "EUR"),
                Decimal(settings.min_invest_usd),
                Decimal(settings.max_invest_usd),
            ),
            self.math,
            self.clock,
            NullUnitOfWorkFactory(),
            settings,  # type: ignore[arg-type]
        )
        rpc = StubRpc()
        rpc.sent = []
        rpc.fail = False
        rpc.balance = 50_000_000_000
        self.rpc = rpc
        self.admin = AdminService(
            self.users,
            self.balances,
            None,  # type: ignore[arg-type]
            self.house_repo,
            StubPriceService(),
            NullUnitOfWorkFactory(),
            rpc,  # type: ignore[arg-type]
            SolanaAddressValidator(),
            self.tx_repo,
            self.investors,
        )

    def seed_user(self, discord_id: str, sol: str = "5") -> UserRecord:
        user = UserRecord(
            discord_id=discord_id,
            sol_balance_lamports=SolAmount.parse(sol).lamports,
            deposit_memo=self.ids.deposit_memo(),
            created_at=self.clock.now(),
            updated_at=self.clock.now(),
        )
        self.users_repo.users[discord_id] = user
        return user


@pytest.mark.asyncio
async def test_investor_owns_ten_percent_and_rides_book() -> None:
    harness = InvestorHarness()
    player = harness.seed_user("inv-1", "5")
    start = player.sol_balance_lamports

    position = await harness.investors.deposit("inv-1", SolAmount.parse("1"))
    assert position.ownership_bps == 1_000
    assert position.user_equity_lamports == 1_000_000_000
    assert harness.house_repo.record.sol_balance_lamports == 10_000_000_000
    user = await harness.users_repo.find_by_discord_id("inv-1")
    assert user is not None
    assert user.sol_balance_lamports == start - 1_000_000_000
    assert not any(entry.type is LedgerEntryType.FEE for entry in harness.ledger_repo.entries)

    await harness.balances.credit_house_fee(
        amount=SolAmount.from_lamports(200_000_000),
        price_usd=Decimal("100"),
        game_id="g1",
    )
    marked = await harness.investors.position("inv-1")
    assert marked.book_lamports == 10_200_000_000
    assert marked.user_equity_lamports == 1_020_000_000

    await harness.balances.debit_house_reserve(
        amount=SolAmount.parse("1"),
        price_usd=Decimal("100"),
        description="House blackjack loss",
        entry_type=LedgerEntryType.WIN,
        transaction_type=TransactionType.WIN,
    )
    after_loss = await harness.investors.position("inv-1")
    assert after_loss.book_lamports == 9_200_000_000
    assert after_loss.user_equity_lamports == 920_000_000

    withdrawn = await harness.investors.withdraw("inv-1", SolAmount.parse("1"))
    assert withdrawn.user_shares == 0
    assert withdrawn.user_equity_lamports == 0
    user = await harness.users_repo.find_by_discord_id("inv-1")
    assert user is not None
    assert user.sol_balance_lamports == start - 1_000_000_000 + 920_000_000


@pytest.mark.asyncio
async def test_empty_house_rejects_invest() -> None:
    harness = InvestorHarness(house_sol="0")
    harness.seed_user("inv-1", "5")
    with pytest.raises(InvestorError, match="house book is empty"):
        await harness.investors.deposit("inv-1", SolAmount.parse("1"))


@pytest.mark.asyncio
async def test_founder_cannot_withdraw_investor_capital() -> None:
    harness = InvestorHarness()
    harness.seed_user("inv-1", "5")
    await harness.investors.deposit("inv-1", SolAmount.parse("1"))

    with pytest.raises(WithdrawalError, match="founder share"):
        await harness.admin.withdraw_house(SolAmount.parse("9.5"), DESTINATION, "admin-1")

    result = await harness.admin.withdraw_house(SolAmount.parse("9"), DESTINATION, "admin-1")
    assert result.amount.lamports == 9_000_000_000
    assert harness.house_repo.record.sol_balance_lamports == 1_000_000_000
    leftover = await harness.investors.position("inv-1")
    assert leftover.user_equity_lamports == 1_000_000_000
