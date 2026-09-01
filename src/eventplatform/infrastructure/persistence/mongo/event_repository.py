"""Canonical event storage.

Two things here carry the architecture:

1. ``insert`` relies on the unique index to decide duplication. It does not check first
   and then write - that loses the race between two workers handling the same redelivery.
2. Paging is keyset, not skip. ``skip`` costs time linear in the offset, so deep pages
   degrade exactly when a customer is exporting the most data.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from eventplatform.domain.event import (
    Attribution,
    Consent,
    Conversion,
    Event,
    Identity,
    Projection,
    ProjectionStatus,
    Source,
)
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.idempotency import InsertOutcome, classify_duplicate
from eventplatform.domain.policies.time_window import AppliedRange, Bucket
from eventplatform.infrastructure.persistence.mongo.indexes import EVENTS
from eventplatform.ports.event_repository import BucketCount, EventFilters, EventPage


def _encode_cursor(occurred_at: datetime, event_id: str) -> str:
    raw = json.dumps({"o": occurred_at.isoformat(), "e": event_id})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    return datetime.fromisoformat(raw["o"]), raw["e"]


def _to_document(event: Event) -> dict[str, Any]:
    return {
        "tenant_id": event.tenant_id,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at,
        "received_at": event.received_at,
        "schema_version": event.schema_version,
        "identity": {
            "anonymous_id": event.identity.anonymous_id,
            "contact_id": event.identity.contact_id,
            "session_id": event.identity.session_id,
        },
        "attribution": {
            "campaign_id": event.attribution.campaign_id,
            "channel": event.attribution.channel,
            "source_url": event.attribution.source_url,
            "conversion": (
                {
                    "goal_id": event.attribution.conversion.goal_id,
                    "value": event.attribution.conversion.value,
                    "currency": event.attribution.conversion.currency,
                }
                if event.attribution.conversion
                else None
            ),
        },
        "consent": {
            "analytics": event.consent.analytics,
            "advertising": event.consent.advertising,
        },
        "source": {
            "producer": event.source.producer,
            "ip_hash": event.source.ip_hash,
            "user_agent_family": event.source.user_agent_family,
        },
        "metadata": event.metadata,
        "content_hash": event.content_hash(),
        "projection": {
            "status": event.projection.status.value,
            "attempts": event.projection.attempts,
            "last_error": event.projection.last_error,
            "updated_at": event.projection.updated_at,
        },
    }


def _aware(value: Any) -> datetime:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _from_document(doc: dict[str, Any]) -> Event:
    identity = doc.get("identity") or {}
    attribution = doc.get("attribution") or {}
    conversion = attribution.get("conversion") or None
    consent = doc.get("consent") or {}
    source = doc.get("source") or {}
    projection = doc.get("projection") or {}
    return Event(
        tenant_id=doc["tenant_id"],
        event_id=doc["event_id"],
        event_type=doc["event_type"],
        occurred_at=_aware(doc["occurred_at"]),
        received_at=_aware(doc["received_at"]),
        identity=Identity(
            anonymous_id=identity.get("anonymous_id"),
            contact_id=identity.get("contact_id"),
            session_id=identity.get("session_id"),
        ),
        attribution=Attribution(
            campaign_id=attribution.get("campaign_id"),
            channel=attribution.get("channel"),
            source_url=attribution.get("source_url"),
            conversion=(
                Conversion(
                    goal_id=conversion.get("goal_id"),
                    value=conversion.get("value"),
                    currency=conversion.get("currency"),
                )
                if conversion
                else None
            ),
        ),
        consent=Consent(analytics=consent.get("analytics"), advertising=consent.get("advertising")),
        source=Source(
            producer=source.get("producer", "http-api"),
            ip_hash=source.get("ip_hash"),
            user_agent_family=source.get("user_agent_family"),
        ),
        metadata=doc.get("metadata") or {},
        schema_version=doc.get("schema_version", 1),
        projection=Projection(
            status=ProjectionStatus(projection.get("status", "pending")),
            attempts=projection.get("attempts", 0),
            last_error=projection.get("last_error"),
            updated_at=projection.get("updated_at"),
        ),
    )


class MongoEventRepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self._events = database[EVENTS]

    async def insert(self, tenant: TenantContext, event: Event) -> InsertOutcome:
        if event.tenant_id != tenant.tenant_id:
            raise ValueError("event tenant does not match the resolved tenant context")
        try:
            await self._events.insert_one(_to_document(event))
        except DuplicateKeyError:
            stored = await self._events.find_one(
                {"tenant_id": tenant.tenant_id, "event_id": event.event_id},
                {"content_hash": 1},
            )
            stored_hash = str(stored["content_hash"]) if stored else None
            return classify_duplicate(incoming_hash=event.content_hash(), stored_hash=stored_hash)
        return InsertOutcome.INSERTED

    async def get(self, tenant: TenantContext, event_id: str) -> Event | None:
        doc = await self._events.find_one({"tenant_id": tenant.tenant_id, "event_id": event_id})
        return _from_document(doc) if doc else None

    async def find_page(
        self,
        tenant: TenantContext,
        filters: EventFilters,
        *,
        cursor: str | None,
        limit: int,
    ) -> EventPage:
        query: dict[str, Any] = {
            "tenant_id": tenant.tenant_id,
            "occurred_at": {"$gte": filters.start, "$lt": filters.end},
        }
        if filters.event_type:
            query["event_type"] = filters.event_type
        if filters.contact_id:
            query["identity.contact_id"] = filters.contact_id
        if filters.anonymous_id:
            query["identity.anonymous_id"] = filters.anonymous_id
        if filters.source_url:
            query["attribution.source_url"] = filters.source_url

        if cursor:
            last_occurred, last_id = _decode_cursor(cursor)
            # Strict "less than" on the composite sort key. Including event_id is what
            # keeps paging correct when many events share a timestamp - and they do,
            # because event traffic clusters.
            query["$or"] = [
                {"occurred_at": {"$lt": last_occurred}},
                {"occurred_at": last_occurred, "event_id": {"$lt": last_id}},
            ]

        docs = (
            await self._events.find(query)
            .sort([("occurred_at", -1), ("event_id", -1)])
            .limit(limit + 1)
            .to_list(limit + 1)
        )
        has_more = len(docs) > limit
        page = [_from_document(d) for d in docs[:limit]]
        next_cursor = (
            _encode_cursor(page[-1].occurred_at, page[-1].event_id) if has_more and page else None
        )
        return EventPage(items=page, next_cursor=next_cursor)

    async def aggregate_counts(
        self,
        tenant: TenantContext,
        applied: AppliedRange,
        bucket: Bucket,
        *,
        event_type: str | None = None,
    ) -> list[BucketCount]:
        match: dict[str, Any] = {
            "tenant_id": tenant.tenant_id,
            "occurred_at": {"$gte": applied.start, "$lt": applied.end},
        }
        if event_type:
            match["event_type"] = event_type
        pipeline: list[dict[str, Any]] = [
            {"$match": match},
            {
                "$group": {
                    "_id": {
                        "event_type": "$event_type",
                        "bucket": {
                            "$dateTrunc": {
                                "date": "$occurred_at",
                                "unit": bucket.value,
                                "startOfWeek": "monday",
                            }
                        },
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id.bucket": 1, "_id.event_type": 1}},
        ]
        cursor = await self._events.aggregate(pipeline)
        rows = await cursor.to_list(None)
        return [
            BucketCount(
                bucket_start=_aware(row["_id"]["bucket"]),
                event_type=row["_id"]["event_type"],
                count=row["count"],
            )
            for row in rows
        ]

    async def count_by_type(
        self, tenant: TenantContext, applied: AppliedRange, *, event_type: str | None = None
    ) -> dict[str, int]:
        match: dict[str, Any] = {
            "tenant_id": tenant.tenant_id,
            "occurred_at": {"$gte": applied.start, "$lt": applied.end},
        }
        if event_type:
            match["event_type"] = event_type
        cursor = await self._events.aggregate(
            [{"$match": match}, {"$group": {"_id": "$event_type", "count": {"$sum": 1}}}]
        )
        rows = await cursor.to_list(None)
        return {row["_id"]: row["count"] for row in rows}

    async def update_projection(
        self, tenant: TenantContext, event_id: str, projection: Projection
    ) -> None:
        await self._events.update_one(
            {"tenant_id": tenant.tenant_id, "event_id": event_id},
            {
                "$set": {
                    "projection.status": projection.status.value,
                    "projection.attempts": projection.attempts,
                    "projection.last_error": projection.last_error,
                    "projection.updated_at": projection.updated_at,
                }
            },
        )

    async def find_pending_projections(self, *, stale_before: datetime, limit: int) -> list[Event]:
        """Cross-tenant by design: the reconciler is infrastructure, not a tenant-facing
        read. It never returns data to a caller - it re-indexes what is already stored."""
        docs = (
            await self._events.find(
                {
                    "projection.status": {"$in": ["pending", "failed"]},
                    "$or": [
                        {"projection.updated_at": {"$lt": stale_before}},
                        {"projection.updated_at": None},
                    ],
                }
            )
            .limit(limit)
            .to_list(limit)
        )
        return [_from_document(d) for d in docs]
