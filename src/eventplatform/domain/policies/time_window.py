"""Time window rules (R15, FR-023).

Unbounded ``occurred_at`` lets one misconfigured client rewrite historical aggregates.
That failure surfaces weeks later as an inexplicable spike in a report, by which point
the bad data is already inside every rollup - so the rejection has to happen at the door.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from eventplatform.domain.errors import ValidationError


class Bucket(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"


BUCKET_DELTAS: dict[Bucket, timedelta] = {
    Bucket.HOUR: timedelta(hours=1),
    Bucket.DAY: timedelta(days=1),
    Bucket.WEEK: timedelta(weeks=1),
}


@dataclass(frozen=True, slots=True)
class TimeWindowPolicy:
    max_future_seconds: int = 300
    max_past_days: int = 90
    max_range_days: int = 92

    def validate_occurred_at(self, occurred_at: datetime, *, now: datetime) -> datetime:
        if occurred_at.tzinfo is None:
            raise ValidationError("occurred_at must carry a timezone", field="occurred_at")
        occurred_at = occurred_at.astimezone(UTC)
        if occurred_at > now + timedelta(seconds=self.max_future_seconds):
            raise ValidationError(
                f"occurred_at is more than {self.max_future_seconds}s in the future",
                field="occurred_at",
            )
        if occurred_at < now - timedelta(days=self.max_past_days):
            raise ValidationError(
                f"occurred_at is more than {self.max_past_days} days in the past",
                field="occurred_at",
            )
        return occurred_at


@dataclass(frozen=True, slots=True)
class AppliedRange:
    """What was actually queried. ``clamped`` makes a narrowed request visible to the
    caller rather than silently returning less than they asked for (US3 scenario 3)."""

    start: datetime
    end: datetime
    clamped: bool


def resolve_range(start: datetime, end: datetime, *, policy: TimeWindowPolicy) -> AppliedRange:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValidationError("range bounds must carry a timezone", field="from")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start:
        raise ValidationError("'to' must be after 'from'", field="to")

    limit = timedelta(days=policy.max_range_days)
    if end - start > limit:
        return AppliedRange(start=end - limit, end=end, clamped=True)
    return AppliedRange(start=start, end=end, clamped=False)


def truncate(moment: datetime, bucket: Bucket) -> datetime:
    moment = moment.astimezone(UTC)
    if bucket is Bucket.HOUR:
        return moment.replace(minute=0, second=0, microsecond=0)
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket is Bucket.DAY:
        return day
    # Week buckets start Monday, matching MongoDB's $dateTrunc default of startOfWeek.
    return day - timedelta(days=day.weekday())


def bucket_starts(applied: AppliedRange, bucket: Bucket) -> list[datetime]:
    """Every bucket start in range, so empty buckets can be zero-filled (US3 scenario 4)."""
    step = BUCKET_DELTAS[bucket]
    cursor = truncate(applied.start, bucket)
    out: list[datetime] = []
    while cursor < applied.end:
        out.append(cursor)
        cursor += step
    return out
