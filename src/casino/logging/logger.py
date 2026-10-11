from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

import structlog

from casino.config.settings import Settings

_correlation: ContextVar[dict[str, str]] = ContextVar("correlation", default={})


class LogContext:
    """Correlation fields attached to every structured log line in the current task."""

    @staticmethod
    def current() -> dict[str, str]:
        return dict(_correlation.get())

    @staticmethod
    @contextmanager
    def bind(**fields: str | None) -> Iterator[None]:
        merged = dict(_correlation.get())
        for key, value in fields.items():
            if value:
                merged[key] = value
        token = _correlation.set(merged)
        try:
            yield
        finally:
            _correlation.reset(token)


class CorrelationInjector:
    def __call__(
        self, _logger: object, _method: str, event_dict: dict[str, object]
    ) -> dict[str, object]:
        event_dict.update(_correlation.get())
        return event_dict


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    for name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    renderer: structlog.typing.Processor
    if settings.log_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            CorrelationInjector(),
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
