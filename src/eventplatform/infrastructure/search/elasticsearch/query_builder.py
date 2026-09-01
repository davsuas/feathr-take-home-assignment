"""Structured query construction (FR-025, FR-033).

The tenant filter is added here, by the builder, and there is no parameter through which
a caller can reach the query body. User terms land in a ``match`` on analysed text, where
Lucene operators are not interpreted - so a caller cannot alter query structure, widen the
scope, or escape the tenant filter no matter what they type.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def build_search_body(
    *,
    tenant_id: str,
    terms: str,
    event_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [{"term": {"tenant_id": tenant_id}}]
    if event_type:
        filters.append({"term": {"event_type": event_type}})
    if start or end:
        rng: dict[str, str] = {}
        if start:
            rng["gte"] = start.isoformat()
        if end:
            rng["lt"] = end.isoformat()
        filters.append({"range": {"occurred_at": rng}})

    return {
        "query": {
            "bool": {
                "filter": filters,
                "must": [
                    {
                        "multi_match": {
                            "query": terms,
                            "fields": ["metadata_text", "source_url.text"],
                            # Explicitly NOT query_string or simple_query_string: those
                            # interpret operators from caller input, which is the whole
                            # class of abuse FR-025 forbids.
                            "type": "best_fields",
                            "operator": "and",
                        }
                    }
                ],
            }
        },
        "sort": [{"occurred_at": "desc"}, {"event_id": "desc"}],
    }
