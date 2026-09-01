"""Dead-letter storage (FR-017, FR-018).

Stores the complete original payload. Replay that needed reconstruction would be replay
that quietly changes the event, and then the dead-letter queue is not a safety net.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from eventplatform.domain.identity import TenantContext
from eventplatform.infrastructure.persistence.mongo.indexes import DEAD_LETTERS
from eventplatform.ports.dead_letter_store import DeadLetter


def _to_document(letter: DeadLetter) -> dict[str, Any]:
    return {
        "tenant_id": letter.tenant_id,
        "event_id": letter.event_id,
        "payload": letter.payload,
        "failure_reason": letter.failure_reason,
        "failure_class": letter.failure_class,
        "attempts": letter.attempts,
        "first_failed_at": letter.first_failed_at,
        "last_failed_at": letter.last_failed_at,
        "replayed_at": letter.replayed_at,
    }


def _from_document(doc: dict[str, Any]) -> DeadLetter:
    return DeadLetter(
        tenant_id=doc["tenant_id"],
        event_id=doc["event_id"],
        payload=doc.get("payload") or {},
        failure_reason=doc.get("failure_reason", ""),
        failure_class=doc.get("failure_class", "unknown"),
        attempts=doc.get("attempts", 0),
        first_failed_at=doc["first_failed_at"],
        last_failed_at=doc["last_failed_at"],
        replayed_at=doc.get("replayed_at"),
    )


class MongoDeadLetterStore:
    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self._collection = database[DEAD_LETTERS]

    async def record(self, tenant: TenantContext, letter: DeadLetter) -> None:
        # first_failed_at belongs only in $setOnInsert: it records when this event began
        # failing, so a later attempt must not overwrite it. Keeping it out of $set is
        # also required by MongoDB, which rejects the same path in both operators.
        document = _to_document(letter)
        first_failed_at = document.pop("first_failed_at")
        await self._collection.update_one(
            {"tenant_id": tenant.tenant_id, "event_id": letter.event_id},
            {"$set": document, "$setOnInsert": {"first_failed_at": first_failed_at}},
            upsert=True,
        )

    async def list(
        self, tenant: TenantContext, *, cursor: str | None, limit: int
    ) -> tuple[list[DeadLetter], str | None]:
        query: dict[str, Any] = {"tenant_id": tenant.tenant_id}
        if cursor:
            query["last_failed_at"] = {
                "$lt": datetime.fromisoformat(base64.urlsafe_b64decode(cursor).decode())
            }
        docs = (
            await self._collection.find(query)
            .sort([("last_failed_at", -1)])
            .limit(limit + 1)
            .to_list(limit + 1)
        )
        has_more = len(docs) > limit
        letters = [_from_document(d) for d in docs[:limit]]
        next_cursor = (
            base64.urlsafe_b64encode(letters[-1].last_failed_at.isoformat().encode()).decode()
            if has_more and letters
            else None
        )
        return letters, next_cursor

    async def get(self, tenant: TenantContext, event_id: str) -> DeadLetter | None:
        doc = await self._collection.find_one({"tenant_id": tenant.tenant_id, "event_id": event_id})
        return _from_document(doc) if doc else None

    async def mark_replayed(self, tenant: TenantContext, event_id: str, *, at: datetime) -> None:
        await self._collection.update_one(
            {"tenant_id": tenant.tenant_id, "event_id": event_id},
            {"$set": {"replayed_at": at}},
        )

    async def count(self, tenant: TenantContext) -> int:
        return await self._collection.count_documents({"tenant_id": tenant.tenant_id})
