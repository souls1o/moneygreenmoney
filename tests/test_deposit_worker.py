from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from casino.services.deposit_service import DepositWatchTarget
from casino.solana_client.rpc import SolanaRpcUnavailableError
from casino.workers.deposit_worker import DepositMonitorWorker
from tests.conftest import FrozenClock


@pytest.mark.asyncio
async def test_deposit_worker_stops_and_backs_off_after_rpc_failure() -> None:
    clock = FrozenClock()
    calls = {"n": 0}

    async def get_signatures(*_args, **_kwargs):
        calls["n"] += 1
        raise SolanaRpcUnavailableError("Signature lookup returned int.")

    deposits = SimpleNamespace(
        watch_targets=AsyncMock(
            return_value=[
                DepositWatchTarget(address="addr1", user_id="1"),
                DepositWatchTarget(address="addr2", user_id="2"),
            ]
        ),
    )
    rpc = SimpleNamespace(is_configured=True, get_signatures=get_signatures)
    cursors = SimpleNamespace(get=AsyncMock(return_value=None))
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock)  # type: ignore[arg-type]

    await worker.tick()
    assert calls["n"] == 1
    await worker.tick()
    assert calls["n"] == 1
    clock.advance(61)
    await worker.tick()
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_deposit_worker_round_robins_wallets() -> None:
    clock = FrozenClock()
    seen: list[str] = []

    async def get_signatures(address, **_kwargs):
        seen.append(address)
        return []

    users = [DepositWatchTarget(address=f"u{i}", user_id=str(i)) for i in range(6)]
    deposits = SimpleNamespace(watch_targets=AsyncMock(return_value=users))
    rpc = SimpleNamespace(is_configured=True, get_signatures=get_signatures)
    cursors = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    settings = SimpleNamespace(deposit_wallets_per_tick=2, deposit_rpc_gap_seconds=0)
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock, settings)  # type: ignore[arg-type]

    await worker.tick()
    await worker.tick()
    assert seen == ["u0", "u1", "u2", "u3"]


@pytest.mark.asyncio
async def test_deposit_worker_skips_known_signatures_without_tx_fetch() -> None:
    clock = FrozenClock()
    parsed = {"n": 0}

    async def ingest_signature(signature, target):
        parsed["n"] += 1
        return None

    deposits = SimpleNamespace(
        watch_targets=AsyncMock(return_value=[DepositWatchTarget(address="addr1", user_id="1")]),
        ingest_signature=ingest_signature,
    )

    async def get_signatures(*_args, **_kwargs):
        return ["sig-known"]

    rpc = SimpleNamespace(is_configured=True, get_signatures=get_signatures)
    cursors = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    settings = SimpleNamespace(deposit_wallets_per_tick=4, deposit_rpc_gap_seconds=0)
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock, settings)  # type: ignore[arg-type]
    await worker.tick()
    assert parsed["n"] == 1
    cursors.set.assert_awaited()


@pytest.mark.asyncio
async def test_deposit_worker_polls_even_when_there_are_no_new_signatures() -> None:
    clock = FrozenClock()
    deposits = SimpleNamespace(
        watch_targets=AsyncMock(
            return_value=[DepositWatchTarget(address="house", is_house=True, user_id="house")]
        ),
    )

    async def get_signatures(*_args, **_kwargs):
        return []

    rpc = SimpleNamespace(is_configured=True, get_signatures=get_signatures)
    cursors = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock)  # type: ignore[arg-type]
    await worker.tick()
    deposits.watch_targets.assert_awaited_once()
