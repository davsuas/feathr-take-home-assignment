from __future__ import annotations

from typing import Protocol

from eventplatform.domain.identity import TenantContext


class TenantDirectory(Protocol):
    async def resolve(self, credential_hash: str) -> TenantContext | None:
        """Return ``None`` identically for unknown and revoked credentials.

        Distinguishing them would let a caller probe which keys exist (FR-034).
        """
