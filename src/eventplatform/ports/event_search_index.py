from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from eventplatform.domain.event import Event
from eventplatform.domain.identity import TenantContext


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Structured input only. There is no field here that accepts a query body, which is
    how FR-025 is enforced: raw DSL cannot be expressed, let alone smuggled through."""

    terms: str
    event_type: str | None = None
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True, slots=True)
class SearchResults:
    items: list[Event]
    total: int
    next_cursor: str | None


class EventSearchIndex(Protocol):
    async def ensure_mapping(self) -> None: ...
    async def index(self, tenant: TenantContext, event: Event) -> None: ...
    async def search(
        self, tenant: TenantContext, query: SearchQuery, *, cursor: str | None, limit: int
    ) -> SearchResults: ...
