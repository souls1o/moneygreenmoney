from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from casino.services.deposit_service import DepositWatchTarget
from casino.solana_client.logs_subscribe import parse_logs_notification
from casino.solana_client.websocket_url import websocket_url_from_rpc
from casino.workers.deposit_stream_worker import DepositStreamWorker


def test_helius_https_rpc_becomes_websocket() -> None:
    url = websocket_url_from_rpc("https://mainnet.helius-rpc.com/?api-key=secret")
    assert url == "wss://mainnet.helius-rpc.com/?api-key=secret"


def test_parse_logs_notification_extracts_signature() -> None:
    note = parse_logs_notification(
        {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {
                "subscription": 7,
                "result": {
                    "context": {"slot": 1},
                    "value": {"signature": "sig-abc", "err": None, "logs": []},
                },
            },
        }
    )
    assert note is not None
    assert note.signature == "sig-abc"
    assert note.subscription == 7
    assert note.err is None


def test_parse_logs_notification_skips_failed_transactions() -> None:
    note = parse_logs_notification(
        {
            "method": "logsNotification",
            "params": {
                "subscription": 1,
                "result": {"value": {"signature": "sig-fail", "err": {"InstructionError": [0, "Custom"]}}},
            },
        }
    )
    assert note is not None
    assert note.err is not None


@pytest.mark.asyncio
async def test_stream_credits_mentioned_deposit_address() -> None:
    ingested: list[tuple[str, str]] = []

    class Deposits:
        async def ingest_signature(self, signature: str, target: DepositWatchTarget):
            ingested.append((signature, target.address))
            return None

        async def recover_swept_deposit(self, target: DepositWatchTarget):
            return None

    worker = DepositStreamWorker(Deposits(), SimpleNamespace(solana_commitment="confirmed"))  # type: ignore[arg-type]
    target = DepositWatchTarget(address="Dep111111111111111111111111111111111111111", user_id="1")
    worker._targets = {target.address: target}
    worker._address_by_sub = {7: target.address}
    note = parse_logs_notification(
        {
            "method": "logsNotification",
            "params": {
                "subscription": 7,
                "result": {"value": {"signature": "sig-dep", "err": None}},
            },
        }
    )
    assert note is not None
    await worker.handle_notification(note)
    assert ingested == [("sig-dep", target.address)]


@pytest.mark.asyncio
async def test_stream_ignores_failed_log() -> None:
    ingested: list[str] = []

    class Deposits:
        async def ingest_signature(self, signature: str, target: DepositWatchTarget):
            ingested.append(signature)
            return None

    worker = DepositStreamWorker(Deposits(), SimpleNamespace())  # type: ignore[arg-type]
    worker._targets = {"A": DepositWatchTarget(address="A", user_id="1")}
    worker._address_by_sub = {1: "A"}
    note = parse_logs_notification(
        {
            "method": "logsNotification",
            "params": {"subscription": 1, "result": {"value": {"signature": "bad", "err": "fail"}}},
        }
    )
    assert note is not None
    await worker.handle_notification(note)
    assert ingested == []


def test_subscribe_payload_roundtrip_json() -> None:
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {"subscription": 3, "result": {"value": {"signature": "x", "err": None}}},
        }
    )
    note = parse_logs_notification(json.loads(raw))
    assert note is not None
    assert note.subscription == 3
