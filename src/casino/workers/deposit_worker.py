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

RPC_BACKOFF = timedelta(seconds=60)


def deposit_cursor_name(address: str) -> str:
    return f"solana_deposit:{address}"


PAGE_SIZE = 25
MAX_PAGES = 8


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
        self._wallets_per_tick = int(getattr(settings, "deposit_wallets_per_tick", 4) or 4)
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
        oldest_first = await self._signatures_oldest_first(target.address, until=until)
        last_complete: str | None = None
        caught_up = True
        if oldest_first:
            for signature in oldest_first:
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
                    self._logger.info(
                        "deposit_signature_pending",
                        txSignature=signature,
                        address=target.address,
                    )
                    break
                except Exception:
                    caught_up = False
                    self._logger.exception(
                        "deposit_signature_failed", txSignature=signature, address=target.address
                    )
                    break
            if last_complete is not None:
                await self._cursors.set(cursor_name, last_complete, self._clock.now())
        if not target.is_house:
            recovered = await self._deposits.recover_swept_deposit(target)
            if recovered is not None:
                self._logger.info(
                    "deposit_auto_credited",
                    txSignature="recovered",
                    userId=recovered.discord_id,
                    address=target.address,
                )
        if caught_up:
            await self._deposits.sweep_target(target)

    async def _signatures_oldest_first(self, address: str, *, until: str | None) -> list[str]:
        tip = await self._rpc.get_signatures(address, limit=PAGE_SIZE)
        older = await self._collect_signatures(address, until=until) if until else []
        seen: set[str] = set()
        newest_first: list[str] = []
        for signature in list(tip) + list(older):
            if not signature or signature in seen:
                continue
            seen.add(signature)
            newest_first.append(signature)
        return list(reversed(newest_first))

    def _schedule_house_sync(self) -> None:
        if self._house_sync_task is not None and not self._house_sync_task.done():
            return
        self._house_sync_task = asyncio.create_task(self._run_house_sync())

    async def _run_house_sync(self) -> None:
        try:
            await self._deposits.sync_house_wallet()
        except Exception:
            self._logger.exception("deposit_house_sync_failed")

    async def _collect_signatures(self, address: str, *, until: str | None) -> list[str]:
        newest_first: list[str] = []
        before: str | None = None
        for _ in range(MAX_PAGES):
            page = await self._rpc.get_signatures(
                address, until=until, before=before, limit=PAGE_SIZE
            )
            if not page:
                break
            newest_first.extend(page)
            if len(page) < PAGE_SIZE:
                break
            before = page[-1]
        return newest_first
