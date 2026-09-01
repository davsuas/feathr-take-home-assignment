"""Credential to tenant resolution (research.md R8, FR-034).

Keys are stored only as SHA-256 hashes, so a leaked dump yields no working credential.
Unknown and revoked both return ``None`` - distinguishing them would let a caller
enumerate which keys exist.
"""

from __future__ import annotations

import hashlib
import secrets

from pymongo.asynchronous.database import AsyncDatabase

from eventplatform.domain.identity import TenantContext
from eventplatform.infrastructure.persistence.mongo.indexes import CREDENTIALS, TENANTS


def hash_credential(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def generate_credential() -> str:
    return f"fk_{secrets.token_urlsafe(32)}"


class MongoTenantDirectory:
    def __init__(self, database: AsyncDatabase[dict[str, object]]) -> None:
        self._db = database

    async def resolve(self, credential_hash: str) -> TenantContext | None:
        doc = await self._db[CREDENTIALS].find_one(
            {"key_hash": credential_hash, "status": "active"}
        )
        if doc is None:
            return None
        return TenantContext(tenant_id=str(doc["tenant_id"]), credential_hash=credential_hash)

    async def issue(self, tenant_id: str, *, label: str = "default") -> str:
        """Mint a credential. The plaintext exists only here and in the caller's hands."""
        from datetime import UTC, datetime

        plaintext = generate_credential()
        now = datetime.now(UTC)
        await self._db[TENANTS].update_one(
            {"tenant_id": tenant_id},
            {"$setOnInsert": {"tenant_id": tenant_id, "status": "active", "created_at": now}},
            upsert=True,
        )
        await self._db[CREDENTIALS].insert_one(
            {
                "key_hash": hash_credential(plaintext),
                "tenant_id": tenant_id,
                "label": label,
                "status": "active",
                "created_at": now,
            }
        )
        return plaintext

    async def revoke(self, credential_hash: str) -> None:
        await self._db[CREDENTIALS].update_one(
            {"key_hash": credential_hash}, {"$set": {"status": "revoked"}}
        )
