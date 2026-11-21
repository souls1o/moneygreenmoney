from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LogNotification:
    signature: str
    subscription: int
    err: object = None


def parse_logs_notification(message: dict[str, Any]) -> LogNotification | None:
    if message.get("method") != "logsNotification":
        return None
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    subscription = params.get("subscription")
    result = params.get("result")
    value = result.get("value") if isinstance(result, dict) else None
    if not isinstance(value, dict) or subscription is None:
        return None
    signature = value.get("signature")
    if not signature:
        return None
    return LogNotification(
        signature=str(signature),
        subscription=int(subscription),
        err=value.get("err"),
    )


def logs_subscribe_params(address: str, commitment: str) -> list[object]:
    return [{"mentions": [address]}, {"commitment": commitment}]
