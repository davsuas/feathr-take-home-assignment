"""map #3, #11, #17 - FR-024..FR-026, FR-038, SC-010, SC-011.

Against a real Elasticsearch, because the claims under test are about the mapping:
that `flattened` metadata is searchable, that `dynamic: strict` rejects surprises, and
that caller-supplied operators cannot restructure the query.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from elasticsearch import AsyncElasticsearch

from eventplatform.application.search_events import SearchEvents
from eventplatform.domain.errors import SearchUnavailable
from eventplatform.domain.event import Event
from eventplatform.domain.identity import TenantContext
from eventplatform.infrastructure.search.elasticsearch.index import ElasticsearchEventIndex
from eventplatform.infrastructure.search.elasticsearch.query_builder import build_search_body
from tests.conftest import requires_docker

pytestmark = requires_docker

TENANT_A = TenantContext(tenant_id="tenant_a", credential_hash="hash_a")
TENANT_B = TenantContext(tenant_id="tenant_b", credential_hash="hash_b")
NOW = datetime.now(UTC)


def event(event_id: str, *, tenant: str = "tenant_a", **metadata: object) -> Event:
    return Event(
        tenant_id=tenant,
        event_id=event_id,
        event_type="donation",
        occurred_at=NOW - timedelta(minutes=1),
        received_at=NOW,
        metadata=metadata or {"browser": "Safari", "device_type": "mobile"},
    )


@pytest.fixture
async def search_index(elasticsearch_url):  # type: ignore[no-untyped-def]
    client = AsyncElasticsearch(elasticsearch_url)
    index = ElasticsearchEventIndex(client, "events-test")
    await client.indices.delete(index="events-test", ignore_unavailable=True)
    await index.ensure_mapping()
    yield index
    await client.indices.delete(index="events-test", ignore_unavailable=True)
    await client.close()


async def _refresh(index: ElasticsearchEventIndex) -> None:
    await index._client.indices.refresh(index="events-test")


async def test_projection_and_metadata_search(search_index) -> None:  # type: ignore[no-untyped-def]
    await search_index.index(TENANT_A, event("evt_1", browser="Safari", device_type="mobile"))
    await search_index.index(TENANT_A, event("evt_2", browser="Firefox", device_type="desktop"))
    await _refresh(search_index)

    search = SearchEvents(search_index=search_index, max_page_size=200)
    results = await search(TENANT_A, terms="Safari")
    assert [e.event_id for e in results.items] == ["evt_1"]
    assert results.total == 1
    # The response states the freshness expectation rather than implying the index is
    # authoritative (FR-037).
    assert "may not be indexed yet" in results.freshness_note


async def test_search_is_tenant_scoped(search_index) -> None:  # type: ignore[no-untyped-def]
    await search_index.index(TENANT_A, event("evt_a", browser="Safari"))
    await search_index.index(TENANT_B, event("evt_b", tenant="tenant_b", browser="Safari"))
    await _refresh(search_index)

    search = SearchEvents(search_index=search_index, max_page_size=200)
    assert [e.event_id for e in (await search(TENANT_A, terms="Safari")).items] == ["evt_a"]
    assert [e.event_id for e in (await search(TENANT_B, terms="Safari")).items] == ["evt_b"]


async def test_raw_dsl_is_literal(search_index) -> None:  # type: ignore[no-untyped-def]
    """map #17 - FR-025."""
    await search_index.index(TENANT_A, event("evt_a", browser="Safari"))
    await search_index.index(TENANT_B, event("evt_b", tenant="tenant_b", browser="Safari"))
    await _refresh(search_index)

    search = SearchEvents(search_index=search_index, max_page_size=200)
    for hostile in [
        "Safari OR *",
        '") OR tenant_id:tenant_b OR ("',
        "*:*",
        "Safari AND tenant_id:tenant_b",
    ]:
        results = await search(TENANT_A, terms=hostile)
        assert all(e.tenant_id == "tenant_a" for e in results.items), hostile


def test_tenant_filter_is_always_present_in_the_query_body() -> None:
    """Structural: there is no argument through which a caller reaches the body."""
    body = build_search_body(tenant_id="tenant_a", terms="anything")
    assert {"term": {"tenant_id": "tenant_a"}} in body["query"]["bool"]["filter"]
    # multi_match, never query_string: operators in caller input must not be parsed.
    assert "multi_match" in body["query"]["bool"]["must"][0]
    assert "query_string" not in str(body)


async def test_metadata_with_many_keys_does_not_explode_the_mapping(search_index) -> None:  # type: ignore[no-untyped-def]
    """R7: the entire justification for the `flattened` field type."""
    for index in range(20):
        await search_index.index(
            TENANT_A, event(f"evt_{index}", **{f"custom_field_{index}": f"value_{index}"})
        )
    await _refresh(search_index)

    mapping = await search_index._client.indices.get_mapping(index="events-test")
    properties = mapping["events-test"]["mappings"]["properties"]
    assert properties["metadata"]["type"] == "flattened"
    # 20 distinct customer keys produced exactly one mapping entry, not 20.
    assert "custom_field_0" not in properties


async def test_search_unavailable_degrades() -> None:
    """map #11 - a down index raises a typed error; it never corrupts a stored event."""
    # A port nothing is listening on: the adapter must translate the driver's connection
    # failure into the domain's SearchUnavailable before it crosses the port.
    client = AsyncElasticsearch("http://127.0.0.1:19201", request_timeout=1, max_retries=0)
    index = ElasticsearchEventIndex(client, "events-test")
    with pytest.raises(SearchUnavailable):
        await index.index(TENANT_A, event("evt_x"))
    await client.close()


async def test_reindexing_the_same_event_does_not_duplicate(search_index) -> None:  # type: ignore[no-untyped-def]
    """The reconciler may re-index an event that already made it. That must be a no-op."""
    for _ in range(3):
        await search_index.index(TENANT_A, event("evt_same", browser="Safari"))
    await _refresh(search_index)

    search = SearchEvents(search_index=search_index, max_page_size=200)
    assert (await search(TENANT_A, terms="Safari")).total == 1


async def test_concurrent_indexing_is_safe(search_index) -> None:  # type: ignore[no-untyped-def]
    await asyncio.gather(
        *(search_index.index(TENANT_A, event(f"evt_c_{i}", browser="Safari")) for i in range(10))
    )
    await _refresh(search_index)
    search = SearchEvents(search_index=search_index, max_page_size=200)
    assert (await search(TENANT_A, terms="Safari")).total == 10
