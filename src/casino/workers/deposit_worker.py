from __future__ import annotations

import asyncio
from datetime import timedelta

from solana.exceptions import SolanaRpcException

from casino.logging.logger import get_logger
from casino.repositories.cursor_repository import CursorRepository
from casino.services.deposit_service import (
    DepositService,
    DepositTransactionPendingError,
    DepositWatchTarget,
)
from casino.solana_client.rpc import SolanaRpcGateway, SolanaRpcUnavailableError
from casino.utils.clock import Clock

RPC_BACKOFF = timedelta(seconds=30)
MAX_AUTO_CREDIT_AGE_SECONDS = 2 * 60 * 60
PAGE_SIZE = 8


def deposit_cursor_name(address: str) -> str:
    return f"solana_deposit:{address}"


class DepositMonitorWorker:
    def __init__(
        self,
        deposit_service: DepositService,
        rpc: SolanaRpcGateway,
        cursors: CursorRepository,
        clock: Clock,
        settings=None,
    ) -> None:
        self._deposits = deposit_service
        self._rpc = rpc
        self._cursors = cursors
        self._clock = clock
        self._cooldown_until = None
        self._rr_index = 0
        self._wallets_per_tick = int(getattr(settings, "deposit_wallets_per_tick", 32) or 32)
        self._rpc_gap = float(getattr(settings, "deposit_rpc_gap_seconds", 0) or 0)
        self._logger = get_logger("casino.worker.deposits")
        self._house_sync_task: asyncio.Task[None] | None = None

    async def tick(self) -> None:
        if not self._rpc.is_configured:
            return
        now = self._clock.now()
        if self._cooldown_until is not None and now < self._cooldown_until:
            return
        try:
            for target in self._batch_targets(await self._deposits.watch_targets()):
                if self._rpc_gap:
                    await asyncio.sleep(self._rpc_gap)
                await self._tick_target(target)
            self._schedule_house_sync()
        except (SolanaRpcException, SolanaRpcUnavailableError) as exc:
            self._cooldown_until = now + RPC_BACKOFF
            cause = getattr(exc, "__cause__", None)
            self._logger.warning(
                "deposit_rpc_unavailable",
                error=str(cause or exc) or type(exc).__name__,
                retrySeconds=int(RPC_BACKOFF.total_seconds()),
            )

    def _batch_targets(self, targets: list[DepositWatchTarget]) -> list[DepositWatchTarget]:
        house = [target for target in targets if target.is_house]
        users = [target for target in targets if not target.is_house]
        if not users:
            return house
        size = min(max(1, self._wallets_per_tick), len(users))
        start = self._rr_index % len(users)
        batch = [users[(start + offset) % len(users)] for offset in range(size)]
        self._rr_index = (start + size) % len(users)
        return house + batch

    async def _tick_target(self, target: DepositWatchTarget) -> None:
        cursor_name = deposit_cursor_name(target.address)
        cursor = await self._cursors.get(cursor_name)
        until = cursor.value if cursor else None
        infos = await self._signature_infos(target.address, limit=PAGE_SIZE)
        if not infos:
            await self._deposits.sweep_target(target)
            return
        min_ts = int(self._clock.now().timestamp()) - MAX_AUTO_CREDIT_AGE_SECONDS
        recent: list[str] = []
        for signature, block_time in infos:
            if until and signature == until:
                break
            if until is None and block_time is None:
                continue
            if block_time is not None and int(block_time) < min_ts:
                break
            recent.append(signature)
        last_complete: str | None = None
        caught_up = True
        for signature in reversed(recent):
            try:
                credited = await self._deposits.ingest_signature(signature, target)
                last_complete = signature
                if credited is not None:
                    self._logger.info(
                        "deposit_auto_credited",
                        txSignature=signature,
                        userId=credited.discord_id,
                        address=target.address,
                    )
            except DepositTransactionPendingError:
                caught_up = False
                break
            except Exception:
                caught_up = False
                self._logger.exception(
                    "deposit_signature_failed", txSignature=signature, address=target.address
                )
                break
        if last_complete is not None:
            await self._cursors.set(cursor_name, last_complete, self._clock.now())
        elif until is None:
            await self._cursors.set(cursor_name, infos[0][0], self._clock.now())
        if caught_up:
            await self._deposits.sweep_target(target)

    async def _signature_infos(
        self,
        address: str,
        *,
        until: str | None = None,
        before: str | None = None,
        limit: int = PAGE_SIZE,
    ) -> list[tuple[str, int | None]]:
        getter = getattr(self._rpc, "get_signature_infos", None)
        if getter is not None:
            return list(await getter(address, until=until, before=before, limit=limit))
        signatures = await self._rpc.get_signatures(
            address, until=until, before=before, limit=limit
        )
        return [(signature, None) for signature in signatures]

    def _schedule_house_sync(self) -> None:
        if self._house_sync_task is not None and not self._house_sync_task.done():
            return
        self._house_sync_task = asyncio.create_task(self._run_house_sync())

    async def _run_house_sync(self) -> None:
        try:
            await self._deposits.sync_house_wallet()
        except Exception:
            self._logger.exception("deposit_house_sync_failed")
