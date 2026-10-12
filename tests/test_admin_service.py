from __future__ import annotations

import pytest

from casino.domain.enums import LedgerEntryType, TransactionType
from casino.errors import ValidationError, WithdrawalError
from casino.services.admin_service import AdminService
from casino.services.balance_service import BalanceService
from casino.services.ledger_service import LedgerService
from casino.solana_client.addresses import SolanaAddressValidator
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import SolAmount
from tests.conftest import FrozenClock, StubPriceService
from tests.fakes import (
    MemoryHouseRepository,
    MemoryLedgerRepository,
    MemoryTransactionRepository,
    MemoryUserRepository,
    NullUnitOfWorkFactory,
)
from tests.test_withdrawal_service import DESTINATION, StubRpc


def _service(*, house_lamports: int = 2_000_000_000):
    clock = FrozenClock()
    ids = IdentifierFactory()
    users_repo = MemoryUserRepository()
    ledger_repo = MemoryLedgerRepository()
    tx_repo = MemoryTransactionRepository()
    house_repo = MemoryHouseRepository()
    house_repo.record.sol_balance_lamports = house_lamports
    ledger = LedgerService(ledger_repo, ids, clock)
    balances = BalanceService(users_repo, tx_repo, house_repo, ledger, ids, clock)
    rpc = StubRpc()
    rpc.sent = []
    rpc.fail = False
    rpc.balance = 10_000_000_000
    service = AdminService(
        None,  # type: ignore[arg-type]
        balances,
        None,  # type: ignore[arg-type]
        house_repo,
        StubPriceService(),
        NullUnitOfWorkFactory(),
        rpc,  # type: ignore[arg-type]
        SolanaAddressValidator(),
        tx_repo,
    )
    return service, house_repo, ledger_repo, rpc, tx_repo


@pytest.mark.asyncio
async def test_house_withdraw_debits_and_sends_full_amount() -> None:
    service, house_repo, ledger_repo, rpc, tx_repo = _service()
    amount = SolAmount.from_lamports(1_000_000_000)

    result = await service.withdraw_house(amount, DESTINATION, "admin-1")

    assert result.destination == DESTINATION
    assert result.amount.lamports == 1_000_000_000
    assert result.signature == "withdraw-sig"
    assert rpc.sent == [(DESTINATION, 1_000_000_000)]
    assert house_repo.record.sol_balance_lamports == 1_000_000_000
    entry = next(item for item in ledger_repo.entries if item.type is LedgerEntryType.WITHDRAWAL)
    assert entry.amount_lamports == -amount.lamports
    tx = next(item for item in tx_repo.items.values() if item.type is TransactionType.WITHDRAWAL)
    assert tx.solana_tx_signature == "withdraw-sig"


@pytest.mark.asyncio
async def test_house_withdraw_refunds_book_when_send_fails() -> None:
    service, house_repo, ledger_repo, rpc, _tx_repo = _service()
    rpc.fail = True

    with pytest.raises(WithdrawalError):
        await service.withdraw_house(SolAmount.from_lamports(500_000_000), DESTINATION, "admin-1")

    assert house_repo.record.sol_balance_lamports == 2_000_000_000
    types = [item.type for item in ledger_repo.entries]
    assert LedgerEntryType.WITHDRAWAL in types
    assert LedgerEntryType.REFUND in types


@pytest.mark.asyncio
async def test_house_withdraw_rejects_more_than_book() -> None:
    service, house_repo, _ledger_repo, rpc, _tx_repo = _service(house_lamports=100)

    with pytest.raises(WithdrawalError, match="house book"):
        await service.withdraw_house(SolAmount.from_lamports(1_000_000_000), DESTINATION, "admin-1")

    assert rpc.sent == []
    assert house_repo.record.sol_balance_lamports == 100


@pytest.mark.asyncio
async def test_house_withdraw_rejects_zero() -> None:
    service, *_rest = _service()
    with pytest.raises(ValidationError):
        await service.withdraw_house(SolAmount.from_lamports(0), DESTINATION, "admin-1")
