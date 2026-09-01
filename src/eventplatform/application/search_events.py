"""Metadata search (FR-024, FR-037)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from eventplatform.domain.event import Event
from eventplatform.domain.identity import TenantContext
from eventplatform.ports.event_search_index import EventSearchIndex, SearchQuery

FRESHNESS_NOTE = (
    "Recently accepted events may not be indexed yet. This endpoint is eventually "
    "consistent; GET /events reflects canonical storage sooner."
)


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    items: list[Event]
    total: int
    next_cursor: str | None
    freshness_note: str = FRESHNESS_NOTE


class SearchEvents:
    def __init__(self, *, search_index: EventSearchIndex, max_page_size: int) -> None:
        self._search = search_index
        self._max_page_size = max_page_size

    async def __call__(
        self,
        tenant: TenantContext,
        *,
        terms: str,
        event_type: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> SearchOutcome:
        results = await self._search.search(
            tenant,
            SearchQuery(terms=terms, event_type=event_type, start=start, end=end),
            cursor=cursor,
            limit=min(limit, self._max_page_size),
        )
        return SearchOutcome(
            items=results.items, total=results.total, next_cursor=results.next_cursor
        )
