from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from casino.services.deposit_service import DepositTransactionPendingError, DepositWatchTarget
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
        sync_house_wallet=AsyncMock(return_value=None),
        sweep_target=AsyncMock(return_value=None),
        recover_swept_deposit=AsyncMock(return_value=None),
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
    deposits = SimpleNamespace(
        watch_targets=AsyncMock(return_value=users),
        sync_house_wallet=AsyncMock(return_value=None),
        sweep_target=AsyncMock(return_value=None),
        recover_swept_deposit=AsyncMock(return_value=None),
    )
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
        sync_house_wallet=AsyncMock(return_value=None),
        sweep_target=AsyncMock(return_value=None),
        recover_swept_deposit=AsyncMock(return_value=None),
    )

    async def get_signatures(*_args, **_kwargs):
        return ["sig-known"]

    rpc = SimpleNamespace(is_configured=True, get_signatures=get_signatures)
    cursors = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    settings = SimpleNamespace(deposit_wallets_per_tick=4, deposit_rpc_gap_seconds=0)
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock, settings)  # type: ignore[arg-type]
    await worker.tick()
    assert parsed["n"] == 0
    cursors.set.assert_awaited()
    if worker._house_sync_task is not None:
        await worker._house_sync_task
    deposits.sync_house_wallet.assert_awaited()


@pytest.mark.asyncio
async def test_deposit_worker_does_not_skip_pending_transaction() -> None:
    clock = FrozenClock()

    async def ingest_signature(signature, target):
        raise DepositTransactionPendingError(signature)

    deposits = SimpleNamespace(
        watch_targets=AsyncMock(return_value=[DepositWatchTarget(address="addr1", user_id="1")]),
        ingest_signature=ingest_signature,
        sync_house_wallet=AsyncMock(return_value=None),
        sweep_target=AsyncMock(return_value=None),
        recover_swept_deposit=AsyncMock(return_value=None),
    )

    async def get_signatures(*_args, **_kwargs):
        return ["sig-new"]

    rpc = SimpleNamespace(is_configured=True, get_signatures=get_signatures)
    cursors = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(value="sig-old")),
        set=AsyncMock(),
    )
    settings = SimpleNamespace(deposit_wallets_per_tick=4, deposit_rpc_gap_seconds=0)
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock, settings)  # type: ignore[arg-type]
    await worker.tick()
    cursors.set.assert_not_awaited()
    deposits.sweep_target.assert_not_awaited()


@pytest.mark.asyncio
async def test_deposit_worker_reprocesses_tip_even_if_cursor_advanced() -> None:
    clock = FrozenClock()
    seen: list[str] = []

    async def ingest_signature(signature, target):
        seen.append(signature)
        return None

    deposits = SimpleNamespace(
        watch_targets=AsyncMock(return_value=[DepositWatchTarget(address="addr1", user_id="1")]),
        ingest_signature=ingest_signature,
        sync_house_wallet=AsyncMock(return_value=None),
        sweep_target=AsyncMock(return_value=None),
        recover_swept_deposit=AsyncMock(return_value=None),
    )

    async def get_signatures(*_args, until=None, **_kwargs):
        if until:
            return ["sig-sweep"]
        return ["sig-sweep", "sig-deposit"]

    rpc = SimpleNamespace(is_configured=True, get_signatures=get_signatures)
    cursors = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(value="sig-deposit")),
        set=AsyncMock(),
    )
    settings = SimpleNamespace(deposit_wallets_per_tick=4, deposit_rpc_gap_seconds=0)
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock, settings)  # type: ignore[arg-type]
    await worker.tick()
    assert seen == ["sig-sweep"]
    deposits.sweep_target.assert_awaited_once()


@pytest.mark.asyncio
async def test_deposit_worker_sweeps_house_when_there_are_no_new_signatures() -> None:
    clock = FrozenClock()
    deposits = SimpleNamespace(
        watch_targets=AsyncMock(
            return_value=[DepositWatchTarget(address="house", is_house=True, user_id="house")]
        ),
        sync_house_wallet=AsyncMock(return_value="sweep-sig"),
        sweep_target=AsyncMock(return_value=None),
        recover_swept_deposit=AsyncMock(return_value=None),
    )

    async def get_signatures(*_args, **_kwargs):
        return []

    rpc = SimpleNamespace(is_configured=True, get_signatures=get_signatures)
    cursors = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock)  # type: ignore[arg-type]
    await worker.tick()
    if worker._house_sync_task is not None:
        await worker._house_sync_task
    deposits.sync_house_wallet.assert_awaited_once()
    deposits.sweep_target.assert_awaited_once()


@pytest.mark.asyncio
async def test_deposit_worker_sweeps_user_when_there_are_no_new_signatures() -> None:
    clock = FrozenClock()
    target = DepositWatchTarget(address="player", user_id="1")
    deposits = SimpleNamespace(
        watch_targets=AsyncMock(return_value=[target]),
        sync_house_wallet=AsyncMock(return_value=None),
        sweep_target=AsyncMock(return_value=None),
        recover_swept_deposit=AsyncMock(return_value=None),
    )

    async def get_signatures(*_args, **_kwargs):
        return []

    rpc = SimpleNamespace(is_configured=True, get_signatures=get_signatures)
    cursors = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock)  # type: ignore[arg-type]
    await worker.tick()
    deposits.sweep_target.assert_awaited_once_with(target)


@pytest.mark.asyncio
async def test_deposit_worker_does_not_ingest_history_before_cursor_seed() -> None:
    clock = FrozenClock()
    ingested: list[str] = []

    async def ingest_signature(signature, target):
        ingested.append(signature)
        return None

    deposits = SimpleNamespace(
        watch_targets=AsyncMock(return_value=[DepositWatchTarget(address="addr1", user_id="1")]),
        ingest_signature=ingest_signature,
        sync_house_wallet=AsyncMock(return_value=None),
        sweep_target=AsyncMock(return_value=None),
    )

    async def get_signatures(*_args, **_kwargs):
        return ["sig-old", "sig-older"]

    rpc = SimpleNamespace(is_configured=True, get_signatures=get_signatures)
    cursors = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock)  # type: ignore[arg-type]
    await worker.tick()
    assert ingested == []
    cursors.set.assert_awaited()


@pytest.mark.asyncio
async def test_deposit_worker_skips_old_signatures() -> None:
    clock = FrozenClock()
    ingested: list[str] = []

    async def ingest_signature(signature, target):
        ingested.append(signature)
        return None

    deposits = SimpleNamespace(
        watch_targets=AsyncMock(return_value=[DepositWatchTarget(address="addr1", user_id="1")]),
        ingest_signature=ingest_signature,
        sync_house_wallet=AsyncMock(return_value=None),
        sweep_target=AsyncMock(return_value=None),
    )
    now = int(clock.now().timestamp())

    async def get_signature_infos(*_args, **_kwargs):
        return [("sig-old", now - 86_400), ("sig-new", now - 30)]

    rpc = SimpleNamespace(is_configured=True, get_signature_infos=get_signature_infos)
    cursors = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(value="sig-cursor")),
        set=AsyncMock(),
    )
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock)  # type: ignore[arg-type]
    await worker.tick()
    assert ingested == ["sig-new"]


@pytest.mark.asyncio
async def test_deposit_worker_credits_recent_history_when_cursor_missing() -> None:
    clock = FrozenClock()
    ingested: list[str] = []

    async def ingest_signature(signature, target, payload=None):
        ingested.append(signature)
        return None

    deposits = SimpleNamespace(
        watch_targets=AsyncMock(return_value=[DepositWatchTarget(address="addr1", user_id="1")]),
        ingest_signature=ingest_signature,
        sync_house_wallet=AsyncMock(return_value=None),
        sweep_target=AsyncMock(return_value=None),
    )
    now = int(clock.now().timestamp())

    async def get_signature_infos(*_args, **_kwargs):
        return [("sig-fresh", now - 20), ("sig-ancient", now - 86_400)]

    rpc = SimpleNamespace(is_configured=True, get_signature_infos=get_signature_infos)
    cursors = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    worker = DepositMonitorWorker(deposits, rpc, cursors, clock)  # type: ignore[arg-type]
    await worker.tick()
    assert ingested == ["sig-fresh"]
    cursors.set.assert_awaited()
