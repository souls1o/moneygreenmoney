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
        )
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
