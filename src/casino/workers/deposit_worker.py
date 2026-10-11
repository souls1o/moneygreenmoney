from __future__ import annotations

from datetime import timedelta

from solana.exceptions import SolanaRpcException

from casino.logging.logger import get_logger
from casino.repositories.cursor_repository import CursorRepository
from casino.services.deposit_service import DepositService, DepositWatchTarget
from casino.solana_client.rpc import SolanaRpcGateway, SolanaRpcUnavailableError
from casino.utils.clock import Clock

RPC_BACKOFF = timedelta(seconds=60)


def deposit_cursor_name(address: str) -> str:
    return f"solana_deposit:{address}"


PAGE_SIZE = 30
MAX_PAGES = 20


class DepositMonitorWorker:
    def __init__(
        self,
        deposit_service: DepositService,
        rpc: SolanaRpcGateway,
        cursors: CursorRepository,
        clock: Clock,
    ) -> None:
        self._deposits = deposit_service
        self._rpc = rpc
        self._cursors = cursors
        self._clock = clock
        self._cooldown_until = None
        self._logger = get_logger("casino.worker.deposits")

    async def tick(self) -> None:
        if not self._rpc.is_configured:
            return
        now = self._clock.now()
        if self._cooldown_until is not None and now < self._cooldown_until:
            return
        try:
            for target in await self._deposits.watch_targets():
                await self._tick_target(target)
        except (SolanaRpcException, SolanaRpcUnavailableError, TypeError, AttributeError) as exc:
            self._cooldown_until = now + RPC_BACKOFF
            cause = getattr(exc, "__cause__", None)
            self._logger.warning(
                "deposit_rpc_unavailable",
                error=str(cause or exc) or type(exc).__name__,
                retrySeconds=int(RPC_BACKOFF.total_seconds()),
            )

    async def _tick_target(self, target: DepositWatchTarget) -> None:
        cursor_name = deposit_cursor_name(target.address)
        cursor = await self._cursors.get(cursor_name)
        until = cursor.value if cursor else None
        newest_first: list[str] = []
        before: str | None = None
        for _ in range(MAX_PAGES):
            page = await self._rpc.get_signatures(
                target.address, until=until, before=before, limit=PAGE_SIZE
            )
            if not page:
                break
            newest_first.extend(page)
            if len(page) < PAGE_SIZE:
                break
            before = page[-1]
        if newest_first:
            for signature in reversed(newest_first):
                try:
                    credited = await self._deposits.ingest_signature(signature, target)
                    if credited is not None:
                        self._logger.info(
                            "deposit_auto_credited",
                            txSignature=signature,
                            userId=credited.discord_id,
                            address=target.address,
                        )
                except Exception:
                    self._logger.exception(
                        "deposit_signature_failed", txSignature=signature, address=target.address
                    )
            await self._cursors.set(cursor_name, newest_first[0], self._clock.now())
        if target.is_house:
            await self._deposits.sweep_house()
        elif target.user_id:
            await self._deposits.sweep_user(target.user_id)
