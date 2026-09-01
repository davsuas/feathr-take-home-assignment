"""Payload to domain event mapping, with the domain rules applied.

This is the single place a raw dict becomes an ``Event``. Keeping it in one function
means the API path and the replay path cannot diverge on validation - a replayed event is
validated exactly as a fresh one was.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from eventplatform.domain.errors import ValidationError
from eventplatform.domain.event import (
    Attribution,
    Consent,
    Conversion,
    Event,
    Identity,
    Source,
)
from eventplatform.domain.event_type import CHANNELS, EVENT_TYPES
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.metadata import validate_metadata
from eventplatform.domain.policies.time_window import TimeWindowPolicy


def _parse_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} is not a valid ISO 8601 timestamp", field=field) from exc


def build_event(
    tenant: TenantContext,
    payload: dict[str, Any],
    *,
    received_at: datetime,
    time_policy: TimeWindowPolicy | None = None,
    metadata_max_bytes: int = 16_384,
    metadata_max_depth: int = 3,
    metadata_max_keys: int = 50,
) -> Event:
    policy = time_policy or TimeWindowPolicy()

    event_type = payload.get("event_type")
    if event_type not in EVENT_TYPES:
        raise ValidationError(f"unknown event_type {event_type!r}", field="event_type")

    occurred_at = policy.validate_occurred_at(
        _parse_datetime(payload.get("occurred_at"), field="occurred_at"), now=received_at
    )

    channel = payload.get("channel")
    if channel is not None and channel not in CHANNELS:
        raise ValidationError(f"unknown channel {channel!r}", field="channel")

    conversion_payload = payload.get("conversion") or None
    conversion = None
    if conversion_payload:
        value = conversion_payload.get("value")
        if value is not None and float(value) < 0:
            raise ValidationError("conversion.value must not be negative", field="conversion.value")
        conversion = Conversion(
            goal_id=conversion_payload.get("goal_id"),
            value=float(value) if value is not None else None,
            currency=conversion_payload.get("currency"),
        )

    consent_payload = payload.get("consent") or {}
    metadata = validate_metadata(
        payload.get("metadata"),
        max_bytes=metadata_max_bytes,
        max_depth=metadata_max_depth,
        max_keys=metadata_max_keys,
    )

    return Event(
        tenant_id=tenant.tenant_id,
        event_id=str(payload["event_id"]),
        event_type=str(event_type),
        occurred_at=occurred_at,
        received_at=received_at,
        identity=Identity(
            anonymous_id=payload.get("anonymous_id"),
            contact_id=payload.get("contact_id"),
            session_id=payload.get("session_id"),
        ),
        attribution=Attribution(
            campaign_id=payload.get("campaign_id"),
            channel=channel,
            source_url=payload.get("source_url"),
            conversion=conversion,
        ),
        consent=Consent(
            analytics=consent_payload.get("analytics"),
            advertising=consent_payload.get("advertising"),
        ),
        source=Source(producer=payload.get("producer", "http-api")),
        metadata=metadata,
    )
