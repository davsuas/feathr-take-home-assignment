"""Filtered, paginated, tenant-scoped listing (FR-020, FR-021)."""

from __future__ import annotations

from datetime import datetime

from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.time_window import TimeWindowPolicy, resolve_range
from eventplatform.ports.event_repository import EventFilters, EventPage, EventRepository


class QueryEvents:
    def __init__(
        self,
        *,
        repository: EventRepository,
        time_policy: TimeWindowPolicy,
        max_page_size: int,
    ) -> None:
        self._repository = repository
        self._time_policy = time_policy
        self._max_page_size = max_page_size

    async def __call__(
        self,
        tenant: TenantContext,
        *,
        start: datetime,
        end: datetime,
        event_type: str | None = None,
        contact_id: str | None = None,
        anonymous_id: str | None = None,
        source_url: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> EventPage:
        applied = resolve_range(start, end, policy=self._time_policy)
        page = await self._repository.find_page(
            tenant,
            EventFilters(
                start=applied.start,
                end=applied.end,
                event_type=event_type,
                contact_id=contact_id,
                anonymous_id=anonymous_id,
                source_url=source_url,
            ),
            cursor=cursor,
            limit=min(limit, self._max_page_size),
        )
        return EventPage(items=page.items, next_cursor=page.next_cursor, applied=applied)
