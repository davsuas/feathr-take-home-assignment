"""Cache key construction (R6, FR-028).

Two properties matter and both are load-bearing for tenant isolation:

1. ``tenant_id`` is a literal key segment, not part of the hash, so an operator reading
   Redis by hand can see who owns a key.
2. Every filter that changes the response is inside the hash, so two distinct requests
   can never collide onto one entry - which is how a cache leaks data across tenants.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

KEY_SCHEMA = "v1"


def _normalize(filters: dict[str, Any]) -> str:
    """Canonical encoding: sorted keys, ``None`` dropped, everything stringified.

    Dropping ``None`` matters - an explicitly-absent filter and an omitted one describe
    the same query and must land on the same key.
    """
    cleaned = {k: str(v) for k, v in sorted(filters.items()) if v is not None}
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":"))


def filter_hash(filters: dict[str, Any]) -> str:
    return hashlib.sha256(_normalize(filters).encode()).hexdigest()[:16]


def realtime_stats_key(tenant_id: str, filters: dict[str, Any]) -> str:
    return f"{KEY_SCHEMA}:{tenant_id}:stats:rt:{filter_hash(filters)}"


def single_flight_key(tenant_id: str, filters: dict[str, Any]) -> str:
    return f"{KEY_SCHEMA}:{tenant_id}:lock:stats:{filter_hash(filters)}"


def rate_limit_key(tenant_id: str, credential_hash: str, window: int) -> str:
    return f"{KEY_SCHEMA}:{tenant_id}:rl:{credential_hash[:16]}:{window}"
