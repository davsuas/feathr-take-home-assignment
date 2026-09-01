"""Correlation identifiers (FR-039).

Kept in context variables so every log line inside a request or a message-processing
scope carries them without each call site having to thread them through.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
event_id_var: ContextVar[str | None] = ContextVar("event_id", default=None)
worker_id_var: ContextVar[str | None] = ContextVar("worker_id", default=None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


@contextmanager
def correlation_scope(
    *,
    request_id: str | None = None,
    tenant_id: str | None = None,
    event_id: str | None = None,
    worker_id: str | None = None,
) -> Iterator[None]:
    tokens = [
        (request_id_var, request_id_var.set(request_id)) if request_id else None,
        (tenant_id_var, tenant_id_var.set(tenant_id)) if tenant_id else None,
        (event_id_var, event_id_var.set(event_id)) if event_id else None,
        (worker_id_var, worker_id_var.set(worker_id)) if worker_id else None,
    ]
    try:
        yield
    finally:
        for entry in tokens:
            if entry is not None:
                var, token = entry
                var.reset(token)


def current_correlation() -> dict[str, str]:
    out: dict[str, str] = {}
    for key, var in (
        ("request_id", request_id_var),
        ("tenant_id", tenant_id_var),
        ("event_id", event_id_var),
        ("worker_id", worker_id_var),
    ):
        value = var.get()
        if value:
            out[key] = value
    return out
