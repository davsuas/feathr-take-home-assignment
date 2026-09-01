"""Event ingestion and listing."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from eventplatform.api.dependencies import ContainerDep, TenantDep
from eventplatform.api.schemas.requests import EventSubmission
from eventplatform.api.schemas.responses import (
    AcceptanceReceiptOut,
    EventOut,
    EventPageOut,
    RangeAppliedOut,
)
from eventplatform.application.mapping import build_event
from eventplatform.domain.policies.time_window import TimeWindowPolicy
from eventplatform.infrastructure.observability.correlation import request_id_var

router = APIRouter(tags=["events"])


@router.post(
    "/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptanceReceiptOut,
    summary="Submit an event for asynchronous processing",
    description=(
        "202 means validated and queued. It does NOT mean stored, searchable, or "
        "reflected in any summary."
    ),
)
async def submit_event(
    submission: EventSubmission,
    tenant: TenantDep,
    container: ContainerDep,
    request: Request,
) -> AcceptanceReceiptOut:
    payload = submission.model_dump(mode="json", exclude_none=True)

    # Validated here, in the request path, so a bad event is rejected with a named field
    # instead of being queued and dead-lettered minutes later where nobody sees it.
    settings = container.settings
    build_event(
        tenant,
        {**payload, "event_id": payload.get("event_id") or "pending"},
        received_at=container.clock.now(),
        time_policy=TimeWindowPolicy(
            max_future_seconds=settings.occurred_at_max_future_seconds,
            max_past_days=settings.occurred_at_max_past_days,
            max_range_days=settings.max_aggregate_range_days,
        ),
        metadata_max_bytes=settings.metadata_max_bytes,
        metadata_max_depth=settings.metadata_max_depth,
        metadata_max_keys=settings.metadata_max_keys,
    )

    receipt = await container.ingest(tenant, payload, trace_id=request_id_var.get())
    return AcceptanceReceiptOut(
        event_id=receipt.event_id,
        status="accepted",
        event_id_origin=receipt.event_id_origin,
        received_at=receipt.received_at,
    )


@router.get("/events", response_model=EventPageOut, summary="List this tenant's events")
async def list_events(
    tenant: TenantDep,
    container: ContainerDep,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
    event_type: Annotated[str | None, Query()] = None,
    contact_id: Annotated[str | None, Query()] = None,
    anonymous_id: Annotated[str | None, Query()] = None,
    source_url: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EventPageOut:
    page = await container.query(
        tenant,
        start=start,
        end=end,
        event_type=event_type,
        contact_id=contact_id,
        anonymous_id=anonymous_id,
        source_url=source_url,
        cursor=cursor,
        limit=limit,
    )
    return EventPageOut(
        items=[EventOut.from_domain(e) for e in page.items],
        next_cursor=page.next_cursor,
        range_applied=(
            RangeAppliedOut(
                start=page.applied.start, end=page.applied.end, clamped=page.applied.clamped
            )
            if page.applied
            else None
        ),
    )
