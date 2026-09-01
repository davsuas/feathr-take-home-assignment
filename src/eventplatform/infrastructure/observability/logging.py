"""Structured logging with redaction (FR-036, Principle VI.2).

The redaction processor is not decoration. Event metadata is customer-supplied and can
contain constituent data; dumping a payload into a log moves personal data into a system
with different retention and access rules than the event store.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

from eventplatform.infrastructure.observability.correlation import current_correlation

REDACTED = "[redacted]"

# Dropped wholesale rather than masked: a masked value still reveals length and shape.
SENSITIVE_KEYS = frozenset(
    {
        "payload",
        "metadata",
        "authorization",
        "api_key",
        "credential",
        "token",
        "password",
        "email",
        "ip",
        "ip_address",
        "user_agent",
    }
)


def _redact(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def _add_correlation(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    event_dict.update(current_correlation())
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_correlation,
            _redact,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
