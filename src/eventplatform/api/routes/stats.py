"""Aggregated and live statistics."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from eventplatform.api.dependencies import ContainerDep, TenantDep
from eventplatform.api.schemas.responses import (
    RangeAppliedOut,
    RealtimeOut,
    StatsBucketOut,
    StatsOut,
)
from eventplatform.domain.policies.time_window import Bucket

router = APIRouter(tags=["stats"])


@router.get("/events/stats", response_model=StatsOut, summary="Counts grouped by type and bucket")
async def event_stats(
    tenant: TenantDep,
    container: ContainerDep,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
    bucket: Annotated[Literal["hour", "day", "week"], Query()],
    event_type: Annotated[str | None, Query()] = None,
) -> StatsOut:
    result = await container.stats(
        tenant, start=start, end=end, bucket=Bucket(bucket), event_type=event_type
    )
    return StatsOut(
        range_applied=RangeAppliedOut(
            start=result.applied.start, end=result.applied.end, clamped=result.applied.clamped
        ),
        buckets=[
            StatsBucketOut(bucket_start=b.bucket_start, event_type=b.event_type, count=b.count)
            for b in result.buckets
        ],
    )


@router.get(
    "/events/stats/realtime",
    response_model=RealtimeOut,
    summary="Lightweight live summary served from cache",
    description="Always answers. Degrades to computation rather than failing if the cache is down.",
)
async def realtime_stats(
    tenant: TenantDep,
    container: ContainerDep,
    window: Annotated[Literal["5m", "1h", "24h"], Query()] = "1h",
    event_type: Annotated[str | None, Query()] = None,
) -> RealtimeOut:
    summary = await container.realtime(tenant, window=window, event_type=event_type)
    return RealtimeOut(
        counts=summary.counts,
        cached=summary.cached,
        age_seconds=summary.age_seconds,
        degraded=summary.degraded,
    )
