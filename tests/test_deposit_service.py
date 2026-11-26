from __future__ import annotations

import asyncio

import pytest

from casino.domain.enums import LedgerEntryType
from casino.domain.records import UserRecord
from casino.errors import AlreadyProcessedError
from casino.services.balance_service import BalanceService
from casino.services.deposit_service import (
    DepositService,
    DepositTransactionPendingError,
    DepositWatchTarget,
)
from casino.services.ledger_service import LedgerService
from casino.services.user_service import UserService
from casino.solana_client.deposit_parser import ParsedIncomingTransfer, SolanaDepositParser
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

    async def collect_house_into_super(self) -> str | None:
        return await self.sweep_to_super(self.house_keypair())

    async def collect_user_into_super(self, discord_id: str) -> str | None:
        return await self.sweep_to_super(self.player_keypair(discord_id))

    async def get_signatures(self, address: str | None = None, **_kwargs) -> list[str]:
        return []

    async def get_balance(self, address: str) -> int:
        return 0

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
    assert credited.sol_balance_lamports == 1_000_000_000
    with pytest.raises(AlreadyProcessedError):
        await service.credit_parsed(user, parsed)
    assert users_repo.users["1"].sol_balance_lamports == 1_000_000_000
    assert house_repo.record.sol_balance_lamports == 0
    assert house_repo.record.total_fees_collected_lamports == 0
    deposits = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.DEPOSIT]
    assert len(deposits) == 1
    fees = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.FEE]
    assert fees == []


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
async def test_house_credit_ignores_super_wallet_settlement() -> None:
    service, _users, house_repo, ledger_repo, rpc, _clock = _service()
    parsed = ParsedIncomingTransfer(
        signature="sig-settle",
        lamports=2_000_000_000,
        destination=rpc.house_address,
        source=rpc.super_address,
        memo=None,
    )
    await service.credit_house(parsed)

    async def get_parsed_transaction(_signature: str):
        return {"ok": True}

    class InternalParser:
        def parse(self, _payload, *, destination: str, signature: str):
            return parsed

    service._rpc.get_parsed_transaction = get_parsed_transaction  # type: ignore[method-assign]
    service._parser = InternalParser()  # type: ignore[assignment]
    credited = await service.ingest_signature(
        parsed.signature,
        DepositWatchTarget(address=rpc.house_address, is_house=True),
    )
    assert credited is None
    assert house_repo.record.sol_balance_lamports == 0
    assert ledger_repo.entries == []


@pytest.mark.asyncio
async def test_known_house_deposit_stays_on_house_wallet() -> None:
    service, _users, _house, _ledger, rpc, _clock = _service()
    parsed = ParsedIncomingTransfer(
        signature="sig-house-stuck",
        lamports=2_000_000_000,
        destination="House1111111111111111111111111111111111111",
        source="s",
        memo=None,
    )
    await service.credit_house(parsed)
    rpc.swept = []
    credited = await service.ingest_signature(
        parsed.signature,
        DepositWatchTarget(address=rpc.house_address, is_house=True),
    )
    assert credited is None
    assert rpc.swept == []


@pytest.mark.asyncio
async def test_known_user_deposit_retries_sweep() -> None:
    service, users_repo, _house, _ledger, rpc, clock = _service()
    user = UserRecord(
        discord_id="1",
        deposit_memo="BBTEST",
        deposit_address="Player111111111111111111111111111111111111",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user
    parsed = ParsedIncomingTransfer(
        signature="sig-user-stuck",
        lamports=1_000_000_000,
        destination=user.deposit_address or "",
        source="s",
        memo=None,
    )
    await service.credit_parsed(user, parsed)
    rpc.swept = []
    credited = await service.ingest_signature(
        parsed.signature,
        DepositWatchTarget(address=user.deposit_address or "", user_id="1"),
    )
    assert credited is None
    await asyncio.gather(*list(service._sweep_tasks), return_exceptions=True)
    assert rpc.swept == ["player-1"]


@pytest.mark.asyncio
async def test_ingest_waits_when_transaction_is_missing() -> None:
    service, users_repo, _house, _ledger, _rpc, clock = _service()
    user = UserRecord(
        discord_id="1",
        deposit_memo="BBTEST",
        deposit_address="Player111111111111111111111111111111111111",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user
    with pytest.raises(DepositTransactionPendingError):
        await service.ingest_signature(
            "sig-missing",
            DepositWatchTarget(address=user.deposit_address or "", user_id="1"),
        )


@pytest.mark.asyncio
async def test_ingest_credits_from_balance_delta_when_parse_misses() -> None:
    service, users_repo, _house, ledger_repo, rpc, clock = _service()
    user = UserRecord(
        discord_id="1",
        deposit_memo="BBTEST",
        deposit_address="Player111111111111111111111111111111111111",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user

    class DeltaOnlyParser:
        def parse(self, _payload, *, destination: str, signature: str):
            return None

        def balance_delta(self, _payload, address: str) -> int:
            return 2_500_000

    async def get_parsed_transaction(_signature: str, **_kwargs):
        return {"meta": {"err": None}}

    service._parser = DeltaOnlyParser()  # type: ignore[assignment]
    service._rpc.get_parsed_transaction = get_parsed_transaction  # type: ignore[method-assign]
    credited = await service.ingest_signature(
        "sig-delta",
        DepositWatchTarget(address=user.deposit_address or "", user_id="1"),
    )
    assert credited is not None
    assert credited.sol_balance_lamports == 2_500_000
    assert any(entry.type is LedgerEntryType.DEPOSIT for entry in ledger_repo.entries)
    await asyncio.gather(*list(service._sweep_tasks), return_exceptions=True)
    assert rpc.swept == ["player-1"]


@pytest.mark.asyncio
async def test_ingest_credits_from_websocket_payload_without_rpc() -> None:
    service, users_repo, _house, ledger_repo, rpc, clock = _service()
    user = UserRecord(
        discord_id="1",
        deposit_memo="BBTEST",
        deposit_address="Player111111111111111111111111111111111111",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user
    payload = {
        "transaction": {
            "message": {"accountKeys": [user.deposit_address, "Sender1111111111111111111111111111111111111"]}
        },
        "meta": {"err": None, "preBalances": [0, 5_000_000], "postBalances": [1_250_000, 3_750_000]},
    }

    async def get_parsed_transaction(_signature: str, **_kwargs):
        raise AssertionError("websocket payload should not fetch the transaction")

    service._parser = SolanaDepositParser()
    service._rpc.get_parsed_transaction = get_parsed_transaction  # type: ignore[method-assign]
    credited = await service.ingest_signature(
        "sig-ws",
        DepositWatchTarget(address=user.deposit_address or "", user_id="1"),
        payload=payload,
    )
    assert credited is not None
    assert credited.sol_balance_lamports == 1_250_000
    assert any(entry.type is LedgerEntryType.DEPOSIT for entry in ledger_repo.entries)


@pytest.mark.asyncio
async def test_ingest_does_not_sweep_uncredited_outgoing_signatures() -> None:
    service, users_repo, _house, _ledger, rpc, clock = _service()
    user = UserRecord(
        discord_id="1",
        deposit_memo="BBTEST",
        deposit_address="Player111111111111111111111111111111111111",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user

    async def get_parsed_transaction(_signature: str):
        return {
            "meta": {"err": None, "preBalances": [1_000_000, 1_000_000], "postBalances": [1_000_000, 1_000_000]},
            "transaction": {
                "message": {
                    "accountKeys": [user.deposit_address, "Other11111111111111111111111111111111111111"],
                    "instructions": [],
                }
            },
        }

    service._rpc.get_parsed_transaction = get_parsed_transaction  # type: ignore[method-assign]
    service._parser = SolanaDepositParser()
    credited = await service.ingest_signature(
        "sig-outgoing",
        DepositWatchTarget(address=user.deposit_address or "", user_id="1"),
    )
    assert credited is None
    await asyncio.gather(*list(service._sweep_tasks), return_exceptions=True)
    assert rpc.swept == []


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
    await asyncio.gather(*list(service._sweep_tasks), return_exceptions=True)
    assert seen == [("1", "sig-listen")]


@pytest.mark.asyncio
async def test_recover_credits_player_after_sweep_when_incoming_did_not_parse() -> None:
    service, users_repo, _house, ledger_repo, rpc, clock = _service()
    user = UserRecord(
        discord_id="1",
        deposit_memo="BBTEST",
        deposit_address="Player111111111111111111111111111111111111",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user
    payloads = {
        "sig-deposit": {"meta": {"err": None}},
        "sig-sweep": {
            "meta": {
                "err": None,
                "preBalances": [2_000_000_000, 1_000_000],
                "postBalances": [5_000, 2_000_995_000],
            },
            "transaction": {
                "message": {
                    "accountKeys": [user.deposit_address, rpc.super_address],
                    "instructions": [],
                }
            },
        },
    }

    async def get_parsed_transaction(signature: str, **_kwargs):
        return payloads.get(signature)

    async def get_signatures(_address: str | None = None, **_kwargs):
        return ["sig-sweep", "sig-deposit"]

    service._rpc.get_parsed_transaction = get_parsed_transaction  # type: ignore[method-assign]
    service._rpc.get_signatures = get_signatures  # type: ignore[method-assign]
    service._parser = SolanaDepositParser()
    credited = await service.recover_swept_deposit(
        DepositWatchTarget(address=user.deposit_address or "", user_id="1")
    )
    assert credited is not None
    assert credited.sol_balance_lamports == 1_999_995_000
    deposits = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.DEPOSIT]
    assert len(deposits) == 1


@pytest.mark.asyncio
async def test_credit_parsed_when_price_unavailable() -> None:
    class BrokenPrice:
        async def get_quote(self, *, allow_stale: bool = True):
            raise RuntimeError("price down")

    service, users_repo, _house, ledger_repo, _rpc, clock = _service()
    service._prices = BrokenPrice()  # type: ignore[assignment]
    user = UserRecord(
        discord_id="1",
        deposit_memo="BBTEST",
        deposit_address="Player111111111111111111111111111111111111",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user
    parsed = ParsedIncomingTransfer(
        signature="sig-noprice",
        lamports=1_000_000,
        destination=user.deposit_address or "",
        source="s",
        memo=None,
    )
    credited = await service.credit_parsed(user, parsed)
    assert credited.sol_balance_lamports == 1_000_000
    assert any(entry.type is LedgerEntryType.DEPOSIT for entry in ledger_repo.entries)


@pytest.mark.asyncio
async def test_reconcile_credits_after_rpc_errors_on_incoming_sig() -> None:
    service, users_repo, _house, ledger_repo, rpc, clock = _service()
    user = UserRecord(
        discord_id="1",
        deposit_memo="BBTEST",
        deposit_address="Player111111111111111111111111111111111111",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    users_repo.users["1"] = user
    sweep = {
        "meta": {
            "err": None,
            "preBalances": [2_000_000_000, 1_000_000],
            "postBalances": [5_000, 2_000_995_000],
        },
        "transaction": {
            "message": {
                "accountKeys": [user.deposit_address, rpc.super_address],
                "instructions": [],
            }
        },
    }

    async def get_parsed_transaction(signature: str, **_kwargs):
        if signature == "sig-deposit":
            raise RuntimeError("helius timeout")
        return sweep if signature == "sig-sweep" else None

    async def get_signatures(_address: str | None = None, **_kwargs):
        return ["sig-sweep", "sig-deposit"]

    service._rpc.get_parsed_transaction = get_parsed_transaction  # type: ignore[method-assign]
    service._rpc.get_signatures = get_signatures  # type: ignore[method-assign]
    service._parser = SolanaDepositParser()
    credited = await service.reconcile_user("1")
    assert credited is not None
    assert credited.sol_balance_lamports == 1_999_995_000
    deposits = [entry for entry in ledger_repo.entries if entry.type is LedgerEntryType.DEPOSIT]
    assert len(deposits) == 1

