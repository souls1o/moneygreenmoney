from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import connect as websocket_connect

from casino.logging.logger import get_logger
from casino.services.deposit_service import (
    DepositService,
    DepositTransactionPendingError,
    DepositWatchTarget,
)
from casino.solana_client.logs_subscribe import LogNotification, logs_subscribe_params, parse_logs_notification

# Helius free: 1,000 subscriptions per WebSocket connection.
MAX_LOG_SUBSCRIPTIONS = 1_000
SUBSCRIBE_REFRESH_SECONDS = 2.0
INGEST_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)


class DepositStreamWorker:
    """Credit deposits from Solana `logsSubscribe` (Helius free WebSockets)."""

    def __init__(
        self,
        deposit_service: DepositService,
        settings,
        *,
        connect: Callable[[str], Any] | None = None,
    ) -> None:
        self._deposits = deposit_service
        self._settings = settings
        self._connect = connect or _default_connect
        self._targets: dict[str, DepositWatchTarget] = {}
        self._sub_by_address: dict[str, int] = {}
        self._address_by_sub: dict[int, str] = {}
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._jobs: set[asyncio.Task[None]] = set()
        self._req_id = 0
        self._ws = None
        self._sync_requested = asyncio.Event()
        self._logger = get_logger("casino.worker.deposit_stream")

    def request_sync(self) -> None:
        self._sync_requested.set()

    async def run(self) -> None:
        delay = 1.0
        while True:
            try:
                await self._run_session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("deposit_stream_disconnected")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def handle_notification(self, notification: LogNotification) -> None:
        if notification.err is not None:
            return
        address = self._address_by_sub.get(notification.subscription)
        if not address:
            return
        target = self._targets.get(address)
        if target is None:
            return
        last_error: Exception | None = None
        for attempt, delay in enumerate(INGEST_RETRY_DELAYS, start=1):
            try:
                credited = await self._deposits.ingest_signature(notification.signature, target)
                if credited is None and not target.is_house:
                    await self._deposits.recover_swept_deposit(target)
                return
            except DepositTransactionPendingError as exc:
                last_error = exc
                await asyncio.sleep(delay)
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(min(delay, 0.25))
        if last_error is not None:
            self._logger.warning(
                "deposit_stream_ingest_failed",
                txSignature=notification.signature,
                address=address,
                error=str(last_error),
            )
            if not target.is_house:
                try:
                    await self._deposits.recover_swept_deposit(target)
                except Exception:
                    self._logger.exception("deposit_stream_recover_failed", address=address)

    async def _run_session(self) -> None:
        url = self._settings.resolved_solana_ws_url()
        async with self._connect(url) as ws:
            self._ws = ws
            self._sub_by_address.clear()
            self._address_by_sub.clear()
            self._pending.clear()
            self._req_id = 0
            self._logger.info("deposit_stream_connected", url=_redact_ws_url(url))
            await self._sync_subscriptions()
            await self._recover_missed_on_connect()
            reader = asyncio.create_task(self._read_loop(ws), name="deposit-stream-read")
            syncer = asyncio.create_task(self._sync_loop(), name="deposit-stream-sync")
            try:
                done, pending = await asyncio.wait(
                    {reader, syncer},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    exc = task.exception() if not task.cancelled() else None
                    if exc is not None:
                        raise exc
            finally:
                self._ws = None
                for job in list(self._jobs):
                    job.cancel()

    async def _read_loop(self, ws) -> None:
        async for raw in ws:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            req_id = message.get("id")
            waiter = self._pending.get(req_id) if req_id is not None else None
            if waiter is not None and not waiter.done():
                waiter.set_result(message)
                continue
            notification = parse_logs_notification(message)
            if notification is None:
                continue
            job = asyncio.create_task(self.handle_notification(notification))
            self._jobs.add(job)
            job.add_done_callback(self._jobs.discard)

    async def _sync_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._sync_requested.wait(), timeout=SUBSCRIBE_REFRESH_SECONDS)
            except TimeoutError:
                pass
            self._sync_requested.clear()
            await self._sync_subscriptions()

    async def _recover_missed_on_connect(self) -> None:
        for target in list(self._targets.values()):
            if target.is_house:
                continue
            try:
                recovered = await self._deposits.recover_swept_deposit(target)
                if recovered is not None:
                    self._logger.info(
                        "deposit_recovered_on_connect",
                        userId=recovered.discord_id,
                        address=target.address,
                    )
            except Exception:
                self._logger.exception("deposit_stream_recover_failed", address=target.address)

    async def _sync_subscriptions(self) -> None:
        targets = await self._deposits.watch_targets()
        house = [target for target in targets if target.is_house]
        users = [target for target in targets if not target.is_house]
        selected = (house + users)[:MAX_LOG_SUBSCRIPTIONS]
        wanted = {target.address: target for target in selected}
        self._targets = wanted
        for address in list(self._sub_by_address):
            if address not in wanted:
                await self._unsubscribe(address)
        for target in selected:
            if target.address not in self._sub_by_address:
                await self._subscribe(target.address)
        if len(targets) > MAX_LOG_SUBSCRIPTIONS:
            self._logger.warning(
                "deposit_stream_subscription_cap",
                watching=len(wanted),
                total=len(targets),
                cap=MAX_LOG_SUBSCRIPTIONS,
            )

    async def _subscribe(self, address: str) -> None:
        result = await self._rpc("logsSubscribe", logs_subscribe_params(address, "processed"))
        sub_id = int(result)
        self._sub_by_address[address] = sub_id
        self._address_by_sub[sub_id] = address
        self._logger.info("deposit_stream_subscribed", address=address, subscription=sub_id)

    async def _unsubscribe(self, address: str) -> None:
        sub_id = self._sub_by_address.pop(address, None)
        if sub_id is None:
            return
        self._address_by_sub.pop(sub_id, None)
        try:
            await self._rpc("logsUnsubscribe", [sub_id])
        except Exception:
            self._logger.warning("deposit_stream_unsubscribe_failed", address=address)

    async def _rpc(self, method: str, params: list[object]) -> Any:
        if self._ws is None:
            raise RuntimeError("Deposit stream is not connected.")
        self._req_id += 1
        req_id = self._req_id
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = waiter
        await self._ws.send(
            json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        )
        try:
            message = await asyncio.wait_for(waiter, timeout=15)
        finally:
            self._pending.pop(req_id, None)
        error = message.get("error")
        if error:
            raise RuntimeError(str(error))
        return message.get("result")


def _default_connect(url: str):
    return websocket_connect(url, ping_interval=20, ping_timeout=20)


def _redact_ws_url(url: str) -> str:
    lower = url.lower()
    marker = "api-key="
    index = lower.find(marker)
    if index < 0:
        return url
    return url[: index + len(marker)] + "***"
