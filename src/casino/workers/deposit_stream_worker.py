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
from casino.solana_client.logs_subscribe import (
    LogNotification,
    logs_subscribe_params,
    parse_logs_notification,
    subscription_id,
    transaction_subscribe_params,
)

MAX_LOG_SUBSCRIPTIONS = 1_000
SUBSCRIBE_REFRESH_SECONDS = 15.0
INGEST_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.35, 0.5, 0.8, 1.2, 2.0)
SLOW_RETRY_DELAYS = (3.0, 8.0, 15.0, 30.0, 45.0)


class DepositStreamWorker:
    """Credit deposits from Solana websocket notifications."""

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
        self._use_transaction_subscribe = True
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
        if await self._ingest(notification, target):
            return
        self._spawn(self._retry_ingest(notification, target))

    async def _ingest(self, notification: LogNotification, target: DepositWatchTarget) -> bool:
        last_error: Exception | None = None
        for delay in INGEST_RETRY_DELAYS:
            try:
                await self._deposits.ingest_signature(
                    notification.signature,
                    target,
                    payload=notification.payload,
                )
                return True
            except DepositTransactionPendingError as exc:
                last_error = exc
                await asyncio.sleep(delay)
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(min(delay, 0.25))
        if last_error is not None:
            self._logger.warning(
                "deposit_stream_ingest_pending",
                txSignature=notification.signature,
                address=target.address,
                error=str(last_error),
            )
        return False

    async def _retry_ingest(self, notification: LogNotification, target: DepositWatchTarget) -> None:
        for delay in SLOW_RETRY_DELAYS:
            await asyncio.sleep(delay)
            try:
                await self._deposits.ingest_signature(
                    notification.signature,
                    target,
                    payload=None,
                )
                self._logger.info(
                    "deposit_stream_ingest_retry_ok",
                    txSignature=notification.signature,
                    address=target.address,
                )
                return
            except DepositTransactionPendingError:
                continue
            except Exception:
                self._logger.exception(
                    "deposit_stream_ingest_retry_failed",
                    txSignature=notification.signature,
                    address=target.address,
                )
                return
        self._logger.warning(
            "deposit_stream_ingest_gave_up",
            txSignature=notification.signature,
            address=target.address,
        )

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
            self._spawn(self.handle_notification(notification))

    def _spawn(self, coro) -> None:
        job = asyncio.create_task(coro)
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
        missing = [target for target in selected if target.address not in self._sub_by_address]
        if missing:
            first, *rest = missing
            await self._subscribe(first.address)
            if rest:
                await asyncio.gather(
                    *(self._subscribe(target.address) for target in rest),
                    return_exceptions=True,
                )
        if len(targets) > MAX_LOG_SUBSCRIPTIONS:
            self._logger.warning(
                "deposit_stream_subscription_cap",
                watching=len(wanted),
                total=len(targets),
                cap=MAX_LOG_SUBSCRIPTIONS,
            )

    async def _subscribe(self, address: str) -> None:
        try:
            if self._use_transaction_subscribe:
                result = await self._rpc(
                    "transactionSubscribe", transaction_subscribe_params(address, "processed")
                )
            else:
                result = await self._rpc("logsSubscribe", logs_subscribe_params(address, "processed"))
        except Exception as exc:
            detail = str(exc).lower()
            if self._use_transaction_subscribe and (
                "method not found" in detail or "invalid request" in detail or "unknown method" in detail
            ):
                self._use_transaction_subscribe = False
                self._logger.warning("deposit_stream_transaction_subscribe_unavailable")
                result = await self._rpc("logsSubscribe", logs_subscribe_params(address, "processed"))
            else:
                self._logger.warning("deposit_stream_subscribe_failed", address=address, error=str(exc))
                return
        try:
            sub_id = subscription_id(result)
        except (TypeError, ValueError):
            self._logger.warning("deposit_stream_subscribe_bad_result", address=address, result=str(result))
            return
        self._sub_by_address[address] = sub_id
        self._address_by_sub[sub_id] = address
        self._logger.info("deposit_stream_subscribed", address=address, subscription=sub_id)

    async def _unsubscribe(self, address: str) -> None:
        sub_id = self._sub_by_address.pop(address, None)
        if sub_id is None:
            return
        self._address_by_sub.pop(sub_id, None)
        method = "transactionUnsubscribe" if self._use_transaction_subscribe else "logsUnsubscribe"
        try:
            await self._rpc(method, [sub_id])
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
