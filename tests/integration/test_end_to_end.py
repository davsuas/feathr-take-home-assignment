"""The full journey through HTTP, exactly as the quickstart describes it.

This is the test that would catch a wiring mistake no unit test can see: the API, the
queue, the worker, MongoDB, Elasticsearch, and Redis all in one flow.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from tests.conftest import requires_docker
from tests.integration.test_api import auth, body

pytestmark = requires_docker

NOW = datetime.now(UTC)
WINDOW = {
    "from": (NOW - timedelta(days=1)).isoformat(),
    "to": (NOW + timedelta(hours=1)).isoformat(),
}


async def test_the_whole_pipeline(api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
    client, container, worker = api
    key_a, key_b = tenant_keys["a"], tenant_keys["b"]

    # 1. Submit. Accepted, not stored.
    accept = await client.post("/events", json=body("evt_e2e"), headers=auth(key_a))
    assert accept.status_code == 202

    # 2. Duplicate submission. Still 202 to the sender - a duplicate is not their problem.
    assert (
        await client.post("/events", json=body("evt_e2e"), headers=auth(key_a))
    ).status_code == 202

    await worker.drain()

    # 3. Exactly one canonical record, despite two deliveries.
    listing = await client.get("/events", params=WINDOW, headers=auth(key_a))
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["event_id"] == "evt_e2e"
    assert items[0]["consent"] == {"analytics": True, "advertising": False}

    # 4. Aggregation sees it.
    stats = await client.get(
        "/events/stats", params={**WINDOW, "bucket": "day"}, headers=auth(key_a)
    )
    assert sum(b["count"] for b in stats.json()["buckets"]) == 1
    assert stats.json()["range_applied"]["clamped"] is False

    # 5. Search finds it once the projection lands.
    await container.search.ensure_mapping()
    await container.search._client.indices.refresh(index="events-api-test")
    search = await client.get("/events/search", params={"q": "Safari"}, headers=auth(key_a))
    assert search.status_code == 200
    assert search.json()["total"] == 1
    assert "may not be indexed yet" in search.json()["freshness_note"]

    # 6. Live summary: miss, then hit.
    first = await client.get("/events/stats/realtime", headers=auth(key_a))
    assert first.json()["cached"] is False
    second = await client.get("/events/stats/realtime", headers=auth(key_a))
    assert second.json()["cached"] is True
    assert second.json()["degraded"] is False

    # 7. Tenant B sees none of it, on every read path.
    assert (await client.get("/events", params=WINDOW, headers=auth(key_b))).json()["items"] == []
    b_stats = await client.get(
        "/events/stats", params={**WINDOW, "bucket": "day"}, headers=auth(key_b)
    )
    assert sum(b["count"] for b in b_stats.json()["buckets"]) == 0
    assert (await client.get("/events/search", params={"q": "Safari"}, headers=auth(key_b))).json()[
        "total"
    ] == 0
    assert (await client.get("/events/stats/realtime", headers=auth(key_b))).json()["counts"] == {}


async def test_sender_omitting_an_id_gets_told_so(api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
    """Spec D2's accepted cost, made visible to the sender rather than hidden."""
    client, _, worker = api
    first = await client.post("/events", json=body(), headers=auth(tenant_keys["a"]))
    second = await client.post("/events", json=body(), headers=auth(tenant_keys["a"]))

    assert first.json()["event_id_origin"] == "platform"
    assert first.json()["event_id"] != second.json()["event_id"]

    await worker.drain()
    listing = await client.get("/events", params=WINDOW, headers=auth(tenant_keys["a"]))
    # Two events, not one: the sender was told which path was taken and can fix it by
    # supplying its own id.
    assert len(listing.json()["items"]) == 2


async def test_concurrent_submissions_of_one_event_yield_one_record(api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
    client, _, worker = api
    await asyncio.gather(
        *(
            client.post("/events", json=body("evt_race"), headers=auth(tenant_keys["a"]))
            for _ in range(10)
        )
    )
    await worker.drain()
    listing = await client.get("/events", params=WINDOW, headers=auth(tenant_keys["a"]))
    assert len(listing.json()["items"]) == 1
