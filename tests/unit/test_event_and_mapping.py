"""Envelope, content hash, and payload mapping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eventplatform.application.mapping import build_event
from eventplatform.domain.errors import ValidationError
from eventplatform.domain.identity import TenantContext

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
TENANT = TenantContext(tenant_id="tenant_a", credential_hash="hash")

BASE = {
    "event_id": "evt_1",
    "event_type": "donation",
    "occurred_at": (NOW - timedelta(minutes=5)).isoformat(),
    "contact_id": "contact_42",
    "metadata": {"browser": "Safari"},
}


def test_builds_a_complete_event() -> None:
    event = build_event(TENANT, BASE, received_at=NOW)
    assert event.tenant_id == "tenant_a"
    assert event.identity.contact_id == "contact_42"
    assert event.received_at == NOW


def test_received_at_is_server_assigned_not_client_supplied() -> None:
    # A client-supplied received_at could hide ingestion lag, making SC-011 unmeasurable.
    event = build_event(TENANT, {**BASE, "received_at": "2020-01-01T00:00:00Z"}, received_at=NOW)
    assert event.received_at == NOW


def test_unknown_event_type_is_rejected_by_name() -> None:
    with pytest.raises(ValidationError) as exc:
        build_event(TENANT, {**BASE, "event_type": "nope"}, received_at=NOW)
    assert exc.value.field == "event_type"


def test_negative_conversion_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="negative"):
        build_event(TENANT, {**BASE, "conversion": {"value": -5, "goal_id": "g"}}, received_at=NOW)


def test_absent_consent_is_not_false() -> None:
    event = build_event(TENANT, BASE, received_at=NOW)
    assert event.consent.analytics is None
    with_consent = build_event(TENANT, {**BASE, "consent": {"analytics": False}}, received_at=NOW)
    assert with_consent.consent.analytics is False


class TestContentHash:
    def test_identical_content_hashes_identically(self) -> None:
        assert (
            build_event(TENANT, BASE, received_at=NOW).content_hash()
            == build_event(TENANT, BASE, received_at=NOW).content_hash()
        )

    def test_redelivery_at_a_later_time_is_not_a_conflict(self) -> None:
        # received_at differs between two deliveries of the same event. Including it in
        # the hash would make every redelivery look like a conflicting duplicate.
        later = build_event(TENANT, BASE, received_at=NOW + timedelta(minutes=10))
        assert later.content_hash() == build_event(TENANT, BASE, received_at=NOW).content_hash()

    def test_different_content_hashes_differently(self) -> None:
        changed = build_event(TENANT, {**BASE, "contact_id": "someone_else"}, received_at=NOW)
        assert changed.content_hash() != build_event(TENANT, BASE, received_at=NOW).content_hash()

    def test_hash_is_tenant_scoped(self) -> None:
        other = TenantContext(tenant_id="tenant_b", credential_hash="h")
        assert (
            build_event(other, BASE, received_at=NOW).content_hash()
            != build_event(TENANT, BASE, received_at=NOW).content_hash()
        )
