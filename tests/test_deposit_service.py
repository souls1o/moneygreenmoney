from __future__ import annotations

import pytest

from casino.domain.enums import LedgerEntryType
from casino.domain.records import UserRecord
from casino.errors import AlreadyProcessedError
from casino.services.balance_service import BalanceService
from casino.services.deposit_service import DepositService
from casino.services.ledger_service import LedgerService
from casino.services.user_service import UserService
from casino.solana_client.deposit_parser import ParsedIncomingTransfer
from casino.utils.identifiers import IdentifierFactory
from casino.utils.text import TextNormalizer
from tests.conftest import FrozenClock, StubPriceService, StubSettings
from tests.fakes import (
    MemoryHouseRepository,
    MemoryLedgerRepository,
    MemoryTransactionRepository,
    MemoryUserRepository,
    NullUnitOfWorkFactory,
)


class StubRpc:
    is_configured = True
    has_house_wallet = True
    house_address = "House1111111111111111111111111111111111111"
    super_address = "Super1111111111111111111111111111111111111"
    swept: list[str] = []

    async def get_parsed_transaction(self, signature: str):
        return None

    async def sweep_to_super(self, source) -> str | None:
        self.swept.append(str(source))
        return "sweep-sig"

    def player_keypair(self, discord_id: str):
        return f"player-{discord_id}"

    def house_keypair(self):
        return "house"


class GameLessParser:
    def parse(self, payload, *, destination: str, signature: str):
        return None


def _service(users_repo=None, house_repo=None, tx_repo=None, ledger_repo=None):
    clock = FrozenClock()
    ids = IdentifierFactory()
    users_repo = users_repo or MemoryUserRepository()
    ledger_repo = ledger_repo or MemoryLedgerRepository()
    tx_repo = tx_repo or MemoryTransactionRepository()
    house_repo = house_repo or MemoryHouseRepository()
    settings = StubSettings()
    users = UserService(users_repo, ids, clock, settings)  # type: ignore[arg-type]
    balances = BalanceService(
        users_repo,
        tx_repo,
        house_repo,
        LedgerService(ledger_repo, ids, clock),
        ids,
        clock,
    )
    rpc = StubRpc()
    rpc.swept = []
    service = DepositService(
        users,
        balances,
        StubPriceService(),
        tx_repo,
        rpc,  # type: ignore[arg-type]
        GameLessParser(),  # type: ignore[arg-type]
        NullUnitOfWorkFactory(),
        TextNormalizer(),
    )
    return service, users_repo, house_repo, ledger_repo, rpc, clock


@pytest.mark.asyncio
async def test_deposit_is_idempotent_on_signature() -> None:
    service, users_repo, house_repo, ledger_repo, _rpc, clock = _service()
    user = UserRecord(
        discord_id="1",
        deposit_memo="BBTEST",
        deposit_address="Player111111111111111111111111111111111111",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user
    parsed = ParsedIncomingTransfer(
        signature="sig-1",
        lamports=1_000_000_000,
        destination=user.deposit_address or "",
        source="s",
        memo=None,
    )
    credited = await service.credit_parsed(user, parsed)
    assert credited.sol_balance_lamports == 990_000_000
    with pytest.raises(AlreadyProcessedError):
        await service.credit_parsed(user, parsed)
    assert users_repo.users["1"].sol_balance_lamports == 990_000_000
    assert house_repo.record.sol_balance_lamports == 10_000_000
    assert house_repo.record.total_fees_collected_lamports == 0
    deposits = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.DEPOSIT]
    assert len(deposits) == 1
    fees = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.FEE]
    assert len(fees) == 1
    assert fees[0].amount_lamports == 10_000_000


@pytest.mark.asyncio
async def test_house_deposit_credits_reserves() -> None:
    service, _users, house_repo, ledger_repo, _rpc, _clock = _service()
    parsed = ParsedIncomingTransfer(
        signature="sig-house",
        lamports=2_000_000_000,
        destination="House1111111111111111111111111111111111111",
        source="s",
        memo=None,
    )
    await service.credit_house(parsed)
    await service.credit_house(parsed)
    assert house_repo.record.sol_balance_lamports == 2_000_000_000
    deposits = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.DEPOSIT]
    assert len(deposits) == 1
    assert deposits[0].amount_lamports == 2_000_000_000


@pytest.mark.asyncio
async def test_deposit_notifies_credit_listener() -> None:
    service, users_repo, _house, _ledger, _rpc, clock = _service()
    user = UserRecord(
        discord_id="1",
        deposit_memo="BBTEST",
        deposit_address="Player111111111111111111111111111111111111",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user
    seen: list[tuple[str, str]] = []

    async def on_credited(credited, parsed, usd_price) -> None:
        seen.append((credited.discord_id, parsed.signature))

    service.set_listeners(on_credited=on_credited)
    parsed = ParsedIncomingTransfer(
        signature="sig-listen",
        lamports=1_000_000_000,
        destination=user.deposit_address or "",
        source="s",
        memo=None,
    )
    await service.credit_parsed(user, parsed)
    assert seen == [("1", "sig-listen")]
