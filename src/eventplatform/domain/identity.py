"""Tenant identity - the isolation boundary made explicit.

``TenantContext`` is never constructed from a request body. It only comes back from the
tenant directory in exchange for a credential, and every tenant-touching port method
requires one as its first argument. A missing scope is therefore a type error at
authoring time rather than a cross-tenant disclosure at runtime (Principle I.3).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: str
    credential_hash: str
