from __future__ import annotations

import pytest

from casino.domain.enums import LedgerEntryType
from casino.domain.records import UserRecord
from casino.errors import InsufficientBalanceError, ValidationError
from casino.services.balance_service import BalanceService
from casino.services.ledger_service import LedgerService
from casino.services.tip_service import TipService
from casino.services.user_service import UserService
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


def _service():
    clock = FrozenClock()
    ids = IdentifierFactory()
    users_repo = MemoryUserRepository()
    ledger_repo = MemoryLedgerRepository()
    tx_repo = MemoryTransactionRepository()
    house_repo = MemoryHouseRepository()
    settings = StubSettings()
    users = UserService(users_repo, ids, clock, settings)  # type: ignore[arg-type]
    ledger = LedgerService(ledger_repo, ids, clock)
    balances = BalanceService(users_repo, tx_repo, house_repo, ledger, ids, clock)
    service = TipService(users, balances, StubPriceService(), NullUnitOfWorkFactory())
    return service, users_repo, ledger_repo, clock, ids


def _seed(users_repo: MemoryUserRepository, clock: FrozenClock, ids: IdentifierFactory, discord_id: str, sol: str) -> None:
    users_repo.users[discord_id] = UserRecord(
        discord_id=discord_id,
        sol_balance_lamports=SolAmount.parse(sol).lamports,
        deposit_memo=ids.deposit_memo(),
        created_at=clock.now(),
        updated_at=clock.now(),
    )


@pytest.mark.asyncio
async def test_tip_moves_balance_between_players() -> None:
    service, users_repo, ledger_repo, clock, ids = _service()
    _seed(users_repo, clock, ids, "1", "10")
    _seed(users_repo, clock, ids, "2", "1")
    amount = SolAmount.parse("2")

    result = await service.send("1", "2", amount)

    assert result.amount.lamports == amount.lamports
    assert users_repo.users["1"].sol_balance_lamports == SolAmount.parse("8").lamports
    assert users_repo.users["2"].sol_balance_lamports == SolAmount.parse("3").lamports
    tips = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.TIP]
    assert len(tips) == 2
    assert {entry.user_id for entry in tips} == {"1", "2"}


@pytest.mark.asyncio
async def test_tip_rejects_self() -> None:
    service, users_repo, _ledger, clock, ids = _service()
    _seed(users_repo, clock, ids, "1", "10")
    with pytest.raises(ValidationError, match="yourself"):
        await service.send("1", "1", SolAmount.parse("1"))


@pytest.mark.asyncio
async def test_tip_rejects_more_than_balance() -> None:
    service, users_repo, _ledger, clock, ids = _service()
    _seed(users_repo, clock, ids, "1", "0.1")
    _seed(users_repo, clock, ids, "2", "1")
    with pytest.raises(InsufficientBalanceError):
        await service.send("1", "2", SolAmount.parse("1"))
    assert users_repo.users["1"].sol_balance_lamports == SolAmount.parse("0.1").lamports
    assert users_repo.users["2"].sol_balance_lamports == SolAmount.parse("1").lamports
