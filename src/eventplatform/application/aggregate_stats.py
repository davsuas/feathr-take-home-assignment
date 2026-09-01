"""Time-bucketed counts (FR-022, FR-023).

Empty buckets are zero-filled here rather than in the database. An omitted bucket reads
as "no data collected"; a zero reads as "nothing happened". Those are different claims
and a reporting product must not confuse them (US3 scenario 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.time_window import (
    AppliedRange,
    Bucket,
    TimeWindowPolicy,
    bucket_starts,
    resolve_range,
)
from eventplatform.ports.event_repository import EventRepository


@dataclass(frozen=True, slots=True)
class StatsBucket:
    bucket_start: datetime
    event_type: str
    count: int


@dataclass(frozen=True, slots=True)
class StatsResult:
    applied: AppliedRange
    buckets: list[StatsBucket]


class AggregateStats:
    def __init__(self, *, repository: EventRepository, time_policy: TimeWindowPolicy) -> None:
        self._repository = repository
        self._time_policy = time_policy

    async def __call__(
        self,
        tenant: TenantContext,
        *,
        start: datetime,
        end: datetime,
        bucket: Bucket,
        event_type: str | None = None,
    ) -> StatsResult:
        applied = resolve_range(start, end, policy=self._time_policy)
        rows = await self._repository.aggregate_counts(
            tenant, applied, bucket, event_type=event_type
        )

        observed_types = sorted({row.event_type for row in rows})
        if event_type:
            observed_types = [event_type]
        counts = {(row.bucket_start, row.event_type): row.count for row in rows}

        filled = [
            StatsBucket(
                bucket_start=start_at,
                event_type=type_name,
                count=counts.get((start_at, type_name), 0),
            )
            for start_at in bucket_starts(applied, bucket)
            for type_name in observed_types
        ]
        return StatsResult(applied=applied, buckets=filled)
