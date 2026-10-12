from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.enums import LedgerEntryType
from casino.domain.records import LedgerRecord, UserRecord
from casino.errors import FeatureDisabledError, InsufficientBalanceError, ValidationError, WithdrawalError
from casino.services.balance_service import BalanceService
from casino.services.ledger_service import LedgerService
from casino.services.user_service import UserService
from casino.services.withdrawal_service import WithdrawalService
from casino.solana_client.addresses import SolanaAddressValidator
from casino.solana_client.rpc import RENT_EXEMPT_FALLBACK_LAMPORTS, SWEEP_FEE_LAMPORTS
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import SolAmount
from tests.conftest import FrozenClock, StubPriceService, StubSettings
from tests.fakes import (
    MemoryHouseRepository,
    MemoryLedgerRepository,
    MemoryTransactionRepository,
    MemoryUserRepository,
    NullUnitOfWorkFactory,
)

DESTINATION = "So11111111111111111111111111111111111111112"


class StubRpc:
    super_address = "Super11111111111111111111111111111111112"
    balance = 10_000_000_000
    sent: list[tuple[str, int]] = []
    fail = False

    async def get_balance(self, address: str) -> int:
        return self.balance

    async def get_rent_exempt_lamports(self, data_len: int = 0) -> int:
        return RENT_EXEMPT_FALLBACK_LAMPORTS

    async def send_from_super(self, destination: str, amount: SolAmount) -> str:
        if self.fail:
            raise RuntimeError("rpc down")
        self.sent.append((destination, amount.lamports))
        return "withdraw-sig"


def _service(*, enabled: bool = True):
    clock = FrozenClock()
    ids = IdentifierFactory()
    users_repo = MemoryUserRepository()
    ledger_repo = MemoryLedgerRepository()
    tx_repo = MemoryTransactionRepository()
    house_repo = MemoryHouseRepository()
    settings = StubSettings()
    settings.enable_withdrawals = enabled
    users = UserService(users_repo, ids, clock, settings)  # type: ignore[arg-type]
    ledger = LedgerService(ledger_repo, ids, clock)
    balances = BalanceService(
        users_repo,
        tx_repo,
        house_repo,
        ledger,
        ids,
        clock,
    )
    rpc = StubRpc()
    rpc.sent = []
    rpc.fail = False
    rpc.balance = 10_000_000_000
    service = WithdrawalService(
        settings,  # type: ignore[arg-type]
        users,
        SolanaAddressValidator(),
        balances,
        ledger,
        StubPriceService(),
        rpc,  # type: ignore[arg-type]
        tx_repo,
        NullUnitOfWorkFactory(),
    )
    return service, users_repo, ledger_repo, tx_repo, rpc, clock, house_repo


def _user(users_repo: MemoryUserRepository, clock: FrozenClock, *, lamports: int = 1_000_000_000) -> UserRecord:
    user = UserRecord(
        discord_id="1",
        sol_balance_lamports=lamports,
        deposit_memo="BBTEST",
        wallet_address=DESTINATION,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user
    return user


def _seed_ledger(ledger_repo: MemoryLedgerRepository, clock: FrozenClock, *, lamports: int) -> None:
    ledger_repo.entries.append(
        LedgerRecord(
            entry_id="ld_seed",
            user_id="1",
            type=LedgerEntryType.DEPOSIT,
            amount_lamports=lamports,
            balance_after_lamports=lamports,
            price_usd_at_time=Decimal("100"),
            description="seed",
            timestamp=clock.now(),
        )
    )


@pytest.mark.asyncio
async def test_withdrawal_disabled_raises() -> None:
    service, users_repo, _ledger, _tx, _rpc, clock, _house = _service(enabled=False)
    _user(users_repo, clock)
    with pytest.raises(FeatureDisabledError):
        await service.request("1", SolAmount.parse("0.1"), DESTINATION)


@pytest.mark.asyncio
async def test_withdrawal_requires_wallet() -> None:
    service, users_repo, _ledger, _tx, _rpc, clock, _house = _service()
    _user(users_repo, clock)
    with pytest.raises(ValidationError, match="destination wallet"):
        await service.request("1", SolAmount.parse("0.1"), "")


@pytest.mark.asyncio
async def test_withdrawal_sends_from_super_and_debits() -> None:
    service, users_repo, ledger_repo, tx_repo, rpc, clock, house_repo = _service()
    _user(users_repo, clock)
    _seed_ledger(ledger_repo, clock, lamports=1_000_000_000)
    seen: list[tuple[str, str]] = []

    async def on_sent(user_id, amount, destination, signature) -> None:
        seen.append((user_id, signature))

    service.set_listener(on_sent)
    result = await service.request("1", SolAmount.parse("0.25"), DESTINATION)
    assert result.signature == "withdraw-sig"
    assert result.destination == DESTINATION
    assert result.amount.lamports == 247_500_000
    assert users_repo.users["1"].sol_balance_lamports == 750_000_000
    assert users_repo.users["1"].total_withdrawn_lamports == 250_000_000
    assert rpc.sent == [(DESTINATION, 247_500_000)]
    assert seen == [("1", "withdraw-sig")]
    assert house_repo.record.sol_balance_lamports == 2_500_000
    assert house_repo.record.total_fees_collected_lamports == 0
    withdrawals = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.WITHDRAWAL]
    assert len(withdrawals) == 1
    fees = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.FEE]
    assert len(fees) == 1
    assert fees[0].amount_lamports == 2_500_000
    stored = await tx_repo.find_by_id(next(iter(tx_repo.items)))
    assert stored is not None
    assert stored.solana_tx_signature == "withdraw-sig"


@pytest.mark.asyncio
async def test_withdrawal_refunds_when_send_fails() -> None:
    service, users_repo, ledger_repo, _tx, rpc, clock, house_repo = _service()
    _user(users_repo, clock)
    _seed_ledger(ledger_repo, clock, lamports=1_000_000_000)
    rpc.fail = True
    with pytest.raises(WithdrawalError, match="refunded"):
        await service.request("1", SolAmount.parse("0.25"), DESTINATION)
    assert users_repo.users["1"].sol_balance_lamports == 1_000_000_000
    assert house_repo.record.sol_balance_lamports == 0
    refunds = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.REFUND]
    assert len(refunds) == 1


@pytest.mark.asyncio
async def test_withdrawal_rejects_more_than_balance() -> None:
    service, users_repo, ledger_repo, _tx, _rpc, clock, _house = _service()
    _user(users_repo, clock, lamports=100)
    _seed_ledger(ledger_repo, clock, lamports=1_000_000_000)
    with pytest.raises(InsufficientBalanceError):
        await service.request("1", SolAmount.parse("0.25"), DESTINATION)


@pytest.mark.asyncio
async def test_withdrawal_rejects_when_super_wallet_is_short() -> None:
    service, users_repo, ledger_repo, _tx, rpc, clock, _house = _service()
    _user(users_repo, clock)
    _seed_ledger(ledger_repo, clock, lamports=1_000_000_000)
    rpc.balance = SWEEP_FEE_LAMPORTS + RENT_EXEMPT_FALLBACK_LAMPORTS
    with pytest.raises(WithdrawalError, match="house wallet"):
        await service.request("1", SolAmount.parse("0.25"), DESTINATION)
    assert users_repo.users["1"].sol_balance_lamports == 1_000_000_000


@pytest.mark.asyncio
async def test_withdrawal_keeps_super_wallet_rent_exempt() -> None:
    service, users_repo, ledger_repo, _tx, rpc, clock, _house = _service()
    _user(users_repo, clock)
    _seed_ledger(ledger_repo, clock, lamports=1_000_000_000)
    amount = SolAmount.parse("0.04533")
    rpc.balance = amount.lamports + SWEEP_FEE_LAMPORTS + 3_248
    with pytest.raises(WithdrawalError, match="house wallet"):
        await service.request("1", amount, DESTINATION)
    assert rpc.sent == []
    assert users_repo.users["1"].sol_balance_lamports == 1_000_000_000


@pytest.mark.asyncio
async def test_withdrawal_rejects_more_than_ledger_total() -> None:
    service, users_repo, ledger_repo, _tx, rpc, clock, _house = _service()
    _user(users_repo, clock, lamports=1_000_000_000)
    _seed_ledger(ledger_repo, clock, lamports=100_000_000)
    with pytest.raises(WithdrawalError, match="ledger balance"):
        await service.request("1", SolAmount.parse("0.25"), DESTINATION)
    assert rpc.sent == []
    assert users_repo.users["1"].sol_balance_lamports == 1_000_000_000
