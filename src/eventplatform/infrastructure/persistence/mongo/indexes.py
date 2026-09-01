"""Index creation (research.md R10).

Every index is led by ``tenant_id``. That is not only for isolation: it is also what
makes a future shard key on ``tenant_id`` a configuration change rather than a redesign.

Deliberately absent, with reasons, in research.md R10: ``source_url``, ``campaign_id``,
any ``metadata.*`` index, and a TTL index.
"""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.asynchronous.database import AsyncDatabase

EVENTS = "events"
DEAD_LETTERS = "dead_letters"
TENANTS = "tenants"
CREDENTIALS = "credentials"

EVENT_INDEXES = [
    # The idempotency guarantee itself. Unique, so two workers racing on the same
    # redelivery cannot both succeed - which a read-then-write check cannot prevent.
    IndexModel(
        [("tenant_id", ASCENDING), ("event_id", ASCENDING)], unique=True, name="uq_tenant_event"
    ),
    IndexModel([("tenant_id", ASCENDING), ("occurred_at", DESCENDING)], name="tenant_occurred"),
    IndexModel(
        [("tenant_id", ASCENDING), ("event_type", ASCENDING), ("occurred_at", DESCENDING)],
        name="tenant_type_occurred",
    ),
    IndexModel(
        [("tenant_id", ASCENDING), ("identity.contact_id", ASCENDING), ("occurred_at", DESCENDING)],
        name="tenant_contact_occurred",
        sparse=True,
    ),
    IndexModel(
        [
            ("tenant_id", ASCENDING),
            ("identity.anonymous_id", ASCENDING),
            ("occurred_at", DESCENDING),
        ],
        name="tenant_anonymous_occurred",
        sparse=True,
    ),
    # Serves the reconciler only; partial so it costs nothing once projections succeed.
    IndexModel(
        [("projection.status", ASCENDING), ("projection.updated_at", ASCENDING)],
        name="projection_pending",
        partialFilterExpression={"projection.status": {"$in": ["pending", "failed"]}},
    ),
]


async def ensure_indexes(database: AsyncDatabase[dict[str, object]]) -> None:
    await database[EVENTS].create_indexes(EVENT_INDEXES)
    await database[DEAD_LETTERS].create_indexes(
        [
            IndexModel(
                [("tenant_id", ASCENDING), ("last_failed_at", DESCENDING)], name="tenant_failed"
            ),
            IndexModel(
                [("tenant_id", ASCENDING), ("event_id", ASCENDING)],
                unique=True,
                name="uq_tenant_dl_event",
            ),
        ]
    )
    await database[TENANTS].create_indexes(
        [IndexModel([("tenant_id", ASCENDING)], unique=True, name="uq_tenant")]
    )
    await database[CREDENTIALS].create_indexes(
        [
            IndexModel([("key_hash", ASCENDING)], unique=True, name="uq_key_hash"),
            IndexModel([("tenant_id", ASCENDING)], name="credential_tenant"),
        ]
    )
