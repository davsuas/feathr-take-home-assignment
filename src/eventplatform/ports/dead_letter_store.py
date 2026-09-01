from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from eventplatform.domain.identity import TenantContext


@dataclass(frozen=True, slots=True)
class DeadLetter:
    tenant_id: str
    event_id: str
    payload: dict[str, Any]
    failure_reason: str
    failure_class: str
    attempts: int
    first_failed_at: datetime
    last_failed_at: datetime
    replayed_at: datetime | None = None


class DeadLetterStore(Protocol):
    """Holds the full original payload so replay needs no reconstruction (FR-018)."""

    async def record(self, tenant: TenantContext, letter: DeadLetter) -> None: ...
    async def list(
        self, tenant: TenantContext, *, cursor: str | None, limit: int
    ) -> tuple[list[DeadLetter], str | None]: ...
    async def get(self, tenant: TenantContext, event_id: str) -> DeadLetter | None: ...
    async def mark_replayed(
        self, tenant: TenantContext, event_id: str, *, at: datetime
    ) -> None: ...
    async def count(self, tenant: TenantContext) -> int: ...
