"""map #5, #6 - SC-003, FR-011..FR-014.

Against a real MongoDB, because the guarantee under test IS the unique index. Proving it
with a fake would prove nothing about the constraint that actually enforces it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eventplatform.domain.event import Event, Identity
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.idempotency import InsertOutcome
from eventplatform.infrastructure.persistence.mongo.event_repository import MongoEventRepository
from tests.conftest import requires_docker

pytestmark = requires_docker

TENANT_A = TenantContext(tenant_id="tenant_a", credential_hash="hash_a")
TENANT_B = TenantContext(tenant_id="tenant_b", credential_hash="hash_b")


# Fixed, not datetime.now(): two deliveries of the SAME event carry the same
# occurred_at. Regenerating it per call would change the content hash and make every
# redelivery look like a conflicting duplicate - which is exactly what the hash is for.
OCCURRED_AT = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)


def make_event(
    event_id: str = "evt_1", *, tenant: str = "tenant_a", contact: str | None = "contact_42"
) -> Event:
    now = datetime.now(UTC)
    return Event(
        tenant_id=tenant,
        event_id=event_id,
        event_type="donation",
        occurred_at=OCCURRED_AT,
        received_at=now,
        identity=Identity(contact_id=contact),
        metadata={"browser": "Safari"},
    )


@pytest.fixture
def repository(mongo_database):  # type: ignore[no-untyped-def]
    return MongoEventRepository(mongo_database)


async def test_triplicate_delivery_single_record(repository) -> None:  # type: ignore[no-untyped-def]
    outcomes = [await repository.insert(TENANT_A, make_event()) for _ in range(3)]
    assert outcomes == [
        InsertOutcome.INSERTED,
        InsertOutcome.DUPLICATE_SUPPRESSED,
        InsertOutcome.DUPLICATE_SUPPRESSED,
    ]


async def test_conflicting_duplicate_keeps_first(repository) -> None:  # type: ignore[no-untyped-def]
    assert await repository.insert(TENANT_A, make_event()) is InsertOutcome.INSERTED
    outcome = await repository.insert(TENANT_A, make_event(contact="someone_else"))
    assert outcome is InsertOutcome.DUPLICATE_CONFLICT

    stored = await repository.get(TENANT_A, "evt_1")
    assert stored is not None
    assert stored.identity.contact_id == "contact_42"  # the first write survived


async def test_same_event_id_in_different_tenants_is_not_a_duplicate(repository) -> None:  # type: ignore[no-untyped-def]
    # Uniqueness is (tenant_id, event_id). Two customers picking the same id must not
    # collide - one tenant could otherwise suppress another's events.
    assert await repository.insert(TENANT_A, make_event()) is InsertOutcome.INSERTED
    assert (
        await repository.insert(TENANT_B, make_event(tenant="tenant_b")) is InsertOutcome.INSERTED
    )


async def test_concurrent_duplicates_cannot_both_insert(repository) -> None:  # type: ignore[no-untyped-def]
    """The race a read-then-write check loses and a unique index wins."""
    import asyncio

    outcomes = await asyncio.gather(
        *(repository.insert(TENANT_A, make_event("evt_race")) for _ in range(8))
    )
    assert outcomes.count(InsertOutcome.INSERTED) == 1
    assert outcomes.count(InsertOutcome.DUPLICATE_SUPPRESSED) == 7


async def test_repository_rejects_a_tenant_mismatch(repository) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="tenant"):
        await repository.insert(TENANT_B, make_event(tenant="tenant_a"))
