from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from eventplatform.domain.event import Event, Projection
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.idempotency import InsertOutcome
from eventplatform.domain.policies.time_window import AppliedRange, Bucket


@dataclass(frozen=True, slots=True)
class EventFilters:
    start: datetime
    end: datetime
    event_type: str | None = None
    contact_id: str | None = None
    anonymous_id: str | None = None
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class EventPage:
    items: list[Event]
    next_cursor: str | None
    # What was actually queried. A silently narrowed range returns fewer rows than the
    # caller asked for with no indication why, which reads as missing data.
    applied: AppliedRange | None = None


@dataclass(frozen=True, slots=True)
class BucketCount:
    bucket_start: datetime
    event_type: str
    count: int


class EventRepository(Protocol):
    """Canonical event storage.

    Note what is absent: there is no ``update`` and no ``delete``. Event immutability
    (FR-010) is enforced by the missing method, not by a rule somebody has to remember.
    ``update_projection`` is the single exception and touches only projection state.
    """

    async def insert(self, tenant: TenantContext, event: Event) -> InsertOutcome: ...

    async def find_page(
        self,
        tenant: TenantContext,
        filters: EventFilters,
        *,
        cursor: str | None,
        limit: int,
    ) -> EventPage: ...

    async def aggregate_counts(
        self,
        tenant: TenantContext,
        applied: AppliedRange,
        bucket: Bucket,
        *,
        event_type: str | None = None,
    ) -> list[BucketCount]: ...

    async def count_by_type(
        self, tenant: TenantContext, applied: AppliedRange, *, event_type: str | None = None
    ) -> dict[str, int]: ...

    async def get(self, tenant: TenantContext, event_id: str) -> Event | None: ...

    async def update_projection(
        self, tenant: TenantContext, event_id: str, projection: Projection
    ) -> None: ...

    async def find_pending_projections(
        self, *, stale_before: datetime, limit: int
    ) -> list[Event]: ...
