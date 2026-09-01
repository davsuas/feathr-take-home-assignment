"""Elasticsearch adapter.

Every driver failure is translated into ``SearchUnavailable`` before it crosses the port,
so the application layer never has to know an Elasticsearch client exists.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch import exceptions as es_exceptions

from eventplatform.domain.errors import SearchUnavailable
from eventplatform.domain.event import Event
from eventplatform.domain.identity import TenantContext
from eventplatform.infrastructure.search.elasticsearch.mappings import (
    INDEX_MAPPINGS,
    INDEX_SETTINGS,
    build_metadata_text,
)
from eventplatform.infrastructure.search.elasticsearch.query_builder import build_search_body
from eventplatform.ports.event_search_index import SearchQuery, SearchResults


def _document(event: Event) -> dict[str, Any]:
    conversion = event.attribution.conversion
    return {
        "tenant_id": event.tenant_id,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "received_at": event.received_at.isoformat(),
        "schema_version": event.schema_version,
        "anonymous_id": event.identity.anonymous_id,
        "contact_id": event.identity.contact_id,
        "session_id": event.identity.session_id,
        "campaign_id": event.attribution.campaign_id,
        "channel": event.attribution.channel,
        "source_url": event.attribution.source_url,
        "conversion_goal_id": conversion.goal_id if conversion else None,
        "conversion_value": conversion.value if conversion else None,
        "conversion_currency": conversion.currency if conversion else None,
        "consent_analytics": event.consent.analytics,
        "consent_advertising": event.consent.advertising,
        "metadata": event.metadata,
        "metadata_text": build_metadata_text(event.metadata),
    }


class ElasticsearchEventIndex:
    def __init__(self, client: AsyncElasticsearch, index_name: str) -> None:
        self._client = client
        self._index = index_name

    async def ensure_mapping(self) -> None:
        try:
            if not await self._client.indices.exists(index=self._index):
                await self._client.indices.create(
                    index=self._index, mappings=INDEX_MAPPINGS, settings=INDEX_SETTINGS
                )
        except es_exceptions.ApiError as exc:
            raise SearchUnavailable(str(exc)) from exc
        except es_exceptions.TransportError as exc:
            raise SearchUnavailable(str(exc)) from exc

    async def index(self, tenant: TenantContext, event: Event) -> None:
        if event.tenant_id != tenant.tenant_id:
            raise ValueError("event tenant does not match the resolved tenant context")
        try:
            await self._client.index(
                index=self._index,
                # Composite id, so re-indexing the same event overwrites rather than
                # duplicating - the projection is naturally idempotent too.
                id=f"{tenant.tenant_id}:{event.event_id}",
                document=_document(event),
                refresh=False,
            )
        except (es_exceptions.ApiError, es_exceptions.TransportError) as exc:
            raise SearchUnavailable(str(exc)) from exc

    async def search(
        self, tenant: TenantContext, query: SearchQuery, *, cursor: str | None, limit: int
    ) -> SearchResults:
        body = build_search_body(
            tenant_id=tenant.tenant_id,
            terms=query.terms,
            event_type=query.event_type,
            start=query.start,
            end=query.end,
        )
        if cursor:
            body["search_after"] = json.loads(base64.urlsafe_b64decode(cursor).decode())
        try:
            response = await self._client.search(index=self._index, size=limit, **body)
        except (es_exceptions.ApiError, es_exceptions.TransportError) as exc:
            raise SearchUnavailable(str(exc)) from exc

        hits = response["hits"]["hits"]
        total = response["hits"]["total"]["value"]
        next_cursor = None
        if len(hits) == limit and hits:
            next_cursor = base64.urlsafe_b64encode(json.dumps(hits[-1]["sort"]).encode()).decode()
        return SearchResults(
            items=[_hit_to_event(h["_source"]) for h in hits],
            total=total,
            next_cursor=next_cursor,
        )


def _hit_to_event(source: dict[str, Any]) -> Event:
    from datetime import datetime

    from eventplatform.domain.event import Attribution, Consent, Conversion, Identity

    return Event(
        tenant_id=source["tenant_id"],
        event_id=source["event_id"],
        event_type=source["event_type"],
        occurred_at=datetime.fromisoformat(source["occurred_at"]),
        received_at=datetime.fromisoformat(source["received_at"]),
        identity=Identity(
            anonymous_id=source.get("anonymous_id"),
            contact_id=source.get("contact_id"),
            session_id=source.get("session_id"),
        ),
        attribution=Attribution(
            campaign_id=source.get("campaign_id"),
            channel=source.get("channel"),
            source_url=source.get("source_url"),
            conversion=Conversion(
                goal_id=source.get("conversion_goal_id"),
                value=source.get("conversion_value"),
                currency=source.get("conversion_currency"),
            ),
        ),
        consent=Consent(
            analytics=source.get("consent_analytics"),
            advertising=source.get("consent_advertising"),
        ),
        metadata=source.get("metadata") or {},
        schema_version=source.get("schema_version", 1),
    )
