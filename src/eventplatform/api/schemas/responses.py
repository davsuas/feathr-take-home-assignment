"""Response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from eventplatform.domain.event import Event


class AcceptanceReceiptOut(BaseModel):
    event_id: str
    status: Literal["accepted"]
    # Tells the sender whether their retry will be deduplicated - the single most useful
    # thing this response can say, given the identifier rule in D2.
    event_id_origin: Literal["sender", "platform"]
    received_at: str


class EventOut(BaseModel):
    event_id: str
    tenant_id: str
    event_type: str
    occurred_at: datetime
    received_at: datetime
    schema_version: int
    anonymous_id: str | None = None
    contact_id: str | None = None
    session_id: str | None = None
    campaign_id: str | None = None
    channel: str | None = None
    source_url: str | None = None
    conversion: dict[str, Any] | None = None
    consent: dict[str, Any] | None = None
    metadata: dict[str, Any] = {}

    @classmethod
    def from_domain(cls, event: Event) -> EventOut:
        conversion = event.attribution.conversion
        return cls(
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            received_at=event.received_at,
            schema_version=event.schema_version,
            anonymous_id=event.identity.anonymous_id,
            contact_id=event.identity.contact_id,
            session_id=event.identity.session_id,
            campaign_id=event.attribution.campaign_id,
            channel=event.attribution.channel,
            source_url=event.attribution.source_url,
            conversion=(
                {
                    "goal_id": conversion.goal_id,
                    "value": conversion.value,
                    "currency": conversion.currency,
                }
                if conversion
                else None
            ),
            consent={
                "analytics": event.consent.analytics,
                "advertising": event.consent.advertising,
            },
            metadata=event.metadata,
        )


class RangeAppliedOut(BaseModel):
    start: datetime
    end: datetime
    clamped: bool


class EventPageOut(BaseModel):
    items: list[EventOut]
    next_cursor: str | None = None
    range_applied: RangeAppliedOut | None = None


class StatsBucketOut(BaseModel):
    bucket_start: datetime
    event_type: str
    count: int


class StatsOut(BaseModel):
    range_applied: RangeAppliedOut
    buckets: list[StatsBucketOut]


class SearchOut(BaseModel):
    items: list[EventOut]
    total: int
    next_cursor: str | None = None
    freshness_note: str


class RealtimeOut(BaseModel):
    counts: dict[str, int]
    cached: bool
    age_seconds: int
    degraded: bool


class DeadLetterOut(BaseModel):
    event_id: str
    failure_reason: str
    failure_class: str
    attempts: int
    first_failed_at: datetime
    last_failed_at: datetime
    replayed_at: datetime | None = None


class DeadLetterPageOut(BaseModel):
    items: list[DeadLetterOut]
    next_cursor: str | None = None
