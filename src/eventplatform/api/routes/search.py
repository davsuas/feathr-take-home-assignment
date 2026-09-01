"""Metadata search."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from eventplatform.api.dependencies import ContainerDep, TenantDep
from eventplatform.api.schemas.responses import EventOut, SearchOut
from eventplatform.domain.errors import SearchUnavailable

router = APIRouter(tags=["search"])


@router.get(
    "/events/search",
    response_model=SearchOut,
    summary="Free-text search across allowlisted event metadata",
    description="Terms are treated literally. Raw query DSL is not accepted.",
)
async def search_events(
    tenant: TenantDep,
    container: ContainerDep,
    q: Annotated[str, Query(min_length=1, max_length=256)],
    event_type: Annotated[str | None, Query()] = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SearchOut:
    if container.search_events is None:
        raise SearchUnavailable("search is not configured")
    outcome = await container.search_events(
        tenant, terms=q, event_type=event_type, start=start, end=end, cursor=cursor, limit=limit
    )
    return SearchOut(
        items=[EventOut.from_domain(e) for e in outcome.items],
        total=outcome.total,
        next_cursor=outcome.next_cursor,
        freshness_note=outcome.freshness_note,
    )
