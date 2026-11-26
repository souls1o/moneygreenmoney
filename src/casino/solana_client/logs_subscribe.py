from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LogNotification:
    signature: str
    subscription: int
    err: object = None
    payload: dict[str, Any] | None = None


def parse_logs_notification(message: dict[str, Any]) -> LogNotification | None:
    method = message.get("method")
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    subscription = params.get("subscription")
    if subscription is None:
        return None
    if method == "transactionNotification":
        return _parse_transaction_notification(params, int(subscription))
    if method != "logsNotification":
        return None
    result = params.get("result")
    value = result.get("value") if isinstance(result, dict) else None
    if not isinstance(value, dict):
        return None
    signature = value.get("signature")
    if not signature:
        return None
    return LogNotification(
        signature=str(signature),
        subscription=int(subscription),
        err=value.get("err"),
    )


def _parse_transaction_notification(params: dict[str, Any], subscription: int) -> LogNotification | None:
    result = params.get("result")
    if not isinstance(result, dict):
        return None
    value = result.get("value") if isinstance(result.get("value"), dict) else result
    signature = value.get("signature") or result.get("signature")
    if not signature:
        return None
    meta = value.get("meta")
    if not isinstance(meta, dict):
        nested = value.get("transaction")
        if isinstance(nested, dict) and isinstance(nested.get("meta"), dict):
            meta = nested.get("meta")
    err = meta.get("err") if isinstance(meta, dict) else value.get("err")
    return LogNotification(
        signature=str(signature),
        subscription=subscription,
        err=err,
        payload=value,
    )


def logs_subscribe_params(address: str, commitment: str) -> list[object]:
    return [{"mentions": [address]}, {"commitment": commitment}]


def transaction_subscribe_params(address: str, commitment: str) -> list[object]:
    return [
        {"vote": False, "failed": False, "accountInclude": [address]},
        {
            "commitment": commitment,
            "encoding": "jsonParsed",
            "transactionDetails": "full",
            "showRewards": False,
            "maxSupportedTransactionVersion": 0,
        },
    ]


def subscription_id(result: object) -> int:
    if isinstance(result, bool):
        raise TypeError("Invalid subscription result")
    if isinstance(result, int):
        return result
    if isinstance(result, dict):
        value = result.get("subscription", result.get("result"))
        return int(value)
    return int(result)  # type: ignore[arg-type]
