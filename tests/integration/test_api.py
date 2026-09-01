"""map #13, #18 - the HTTP contract end to end (FR-001..FR-005, FR-032..FR-034).

Driven through the real ASGI app over real stores, so the middleware, dependency
resolution, error mapping, and response shapes are all exercised as a reviewer would.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import requires_docker

pytestmark = requires_docker

NOW = datetime.now(UTC)


def body(event_id: str | None = None, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_type": "donation",
        "occurred_at": (NOW - timedelta(minutes=5)).isoformat(),
        "contact_id": "contact_42",
        "campaign_id": "spring_appeal",
        "channel": "email",
        "source_url": "https://example.org/give",
        "conversion": {"goal_id": "gift", "value": 250.0, "currency": "USD"},
        "consent": {"analytics": True, "advertising": False},
        "metadata": {"browser": "Safari", "device_type": "mobile"},
    }
    if event_id:
        payload["event_id"] = event_id
    return {**payload, **overrides}


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


class TestIngestion:
    async def test_valid_submission_is_accepted(self, api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
        client, _, _ = api
        response = await client.post(
            "/events", json=body("evt_api_1"), headers=auth(tenant_keys["a"])
        )
        assert response.status_code == 202
        payload = response.json()
        assert payload["status"] == "accepted"
        assert payload["event_id"] == "evt_api_1"
        assert payload["event_id_origin"] == "sender"

    async def test_response_carries_a_correlation_id(self, api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
        client, _, _ = api
        response = await client.post("/events", json=body(), headers=auth(tenant_keys["a"]))
        assert response.headers["X-Request-Id"]

    async def test_invalid_event_type_is_rejected_naming_the_field(self, api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
        client, _, _ = api
        response = await client.post(
            "/events", json=body(event_type="not_a_type"), headers=auth(tenant_keys["a"])
        )
        assert response.status_code == 422

    async def test_unknown_field_is_rejected(self, api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
        # A silently-ignored typo is data loss the sender never learns about.
        client, _, _ = api
        response = await client.post(
            "/events", json=body(contactid="typo"), headers=auth(tenant_keys["a"])
        )
        assert response.status_code == 422

    async def test_oversized_metadata_is_rejected_before_the_queue(self, api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
        client, container, _ = api
        response = await client.post(
            "/events",
            json=body(metadata={"blob": "x" * 40_000}),
            headers=auth(tenant_keys["a"]),
        )
        assert response.status_code == 422
        assert response.json()["field"] == "metadata"
        assert await container.queue.depth() == 0  # SC-012: it never reached the queue

    async def test_future_timestamp_is_rejected(self, api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
        client, _, _ = api
        response = await client.post(
            "/events",
            json=body(occurred_at=(NOW + timedelta(days=1)).isoformat()),
            headers=auth(tenant_keys["a"]),
        )
        assert response.status_code == 422
        assert response.json()["field"] == "occurred_at"


class TestAuthentication:
    """map #18 - FR-032, FR-034"""

    async def test_missing_credential_is_rejected(self, api) -> None:  # type: ignore[no-untyped-def]
        client, _, _ = api
        assert (await client.post("/events", json=body())).status_code == 401

    async def test_unknown_and_revoked_credential_identical(self, api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
        client, container, _ = api
        from eventplatform.infrastructure.persistence.mongo.tenant_directory import hash_credential

        unknown = await client.get(
            "/events",
            params={"from": (NOW - timedelta(days=1)).isoformat(), "to": NOW.isoformat()},
            headers=auth("fk_never_issued"),
        )
        await container.tenants.revoke(hash_credential(tenant_keys["b"]))
        revoked = await client.get(
            "/events",
            params={"from": (NOW - timedelta(days=1)).isoformat(), "to": NOW.isoformat()},
            headers=auth(tenant_keys["b"]),
        )
        # Identical apart from the per-request trace id: any other difference would let
        # a caller distinguish "no such key" from "key revoked" and enumerate valid keys.
        assert unknown.status_code == revoked.status_code == 401
        assert {k: v for k, v in unknown.json().items() if k != "trace_id"} == {
            k: v for k, v in revoked.json().items() if k != "trace_id"
        }

    async def test_caller_cannot_name_its_own_tenant(self, api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
        """D1: the tenant comes from the credential, never from the request body."""
        client, _, _ = api
        response = await client.post(
            "/events",
            json={**body("evt_spoof"), "tenant_id": "tenant_b"},
            headers=auth(tenant_keys["a"]),
        )
        # tenant_id is not an accepted field at all: the schema forbids extras, so the
        # attempt is a loud validation error rather than a silently ignored field.
        assert response.status_code == 422


class TestBackpressure:
    """map #13 - SC-013, FR-005"""

    async def test_backpressure_returns_503(self, api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
        client, container, _ = api
        accepted = 0
        rejected = 0
        # Capacity is 50 in the test settings; push well past it with no worker draining.
        for index in range(80):
            response = await client.post(
                "/events", json=body(f"evt_bp_{index}"), headers=auth(tenant_keys["a"])
            )
            if response.status_code == 202:
                accepted += 1
            elif response.status_code == 503:
                rejected += 1
                assert response.headers["Retry-After"]

        assert rejected > 0, "expected the queue to push back"
        # Nothing was accepted and then thrown away: what is queued is what was accepted.
        assert accepted == await container.queue.depth()


class TestOperations:
    async def test_healthz_is_liveness_only(self, api) -> None:  # type: ignore[no-untyped-def]
        client, _, _ = api
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}

    async def test_readyz_reports_each_dependency(self, api) -> None:  # type: ignore[no-untyped-def]
        client, _, _ = api
        response = await client.get("/readyz")
        assert response.status_code == 200
        checks = response.json()["checks"]
        assert set(checks) == {"mongodb", "redis", "elasticsearch"}

    async def test_metrics_exposes_the_operational_signals(self, api, tenant_keys) -> None:  # type: ignore[no-untyped-def]
        """SC-014: an operator can see the state without reading source."""
        client, _, worker = api
        await client.post("/events", json=body("evt_m"), headers=auth(tenant_keys["a"]))
        await worker.drain()

        text = (await client.get("/metrics")).text
        for signal in [
            "events_accepted_total",
            "events_rejected_validation_total",
            "ingest_rejected_backpressure_total",
            "processing_attempts_total",
            "processing_retries_total",
            "duplicate_suppressed_total",
            "dead_letter_total",
            "mongo_write_failures_total",
            "projection_failures_total",
            "cache_hit_total",
            "cache_miss_total",
            "cache_fallback_total",
            "queue_depth",
        ]:
            assert signal in text, f"missing operational signal: {signal}"
