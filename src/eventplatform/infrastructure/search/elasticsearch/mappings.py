"""Elasticsearch mapping (research.md R7).

The decision that matters: customer metadata maps to a single ``flattened`` field. A
customer inventing a thousand metadata keys therefore creates one mapping entry, not a
thousand - which is how mapping explosion is prevented without rejecting customer data.

``dynamic: strict`` makes an unmapped field a loud error at index time rather than a
silent mapping addition nobody notices until the cluster is unhappy.

The trade-off, stated so nobody is surprised later: ``flattened`` indexes every value as
a keyword. No range queries and no per-field analysis inside it. That is why the
allowlisted ``metadata_text`` copy exists alongside.
"""

from __future__ import annotations

from typing import Any

METADATA_TEXT_ALLOWLIST = ("browser", "device_type", "os", "referrer", "campaign_name", "notes")

INDEX_SETTINGS: dict[str, Any] = {
    "number_of_shards": 1,
    "number_of_replicas": 0,
}

INDEX_MAPPINGS: dict[str, Any] = {
    "dynamic": "strict",
    "properties": {
        "tenant_id": {"type": "keyword"},
        "event_id": {"type": "keyword"},
        "event_type": {"type": "keyword"},
        "occurred_at": {"type": "date"},
        "received_at": {"type": "date"},
        "schema_version": {"type": "integer"},
        "anonymous_id": {"type": "keyword"},
        "contact_id": {"type": "keyword"},
        "session_id": {"type": "keyword"},
        "campaign_id": {"type": "keyword"},
        "channel": {"type": "keyword"},
        # Exact match for filtering, plus a text sub-field for the occasional
        # "which pages mention /donate" question.
        "source_url": {"type": "keyword", "fields": {"text": {"type": "text"}}},
        "conversion_goal_id": {"type": "keyword"},
        "conversion_value": {"type": "scaled_float", "scaling_factor": 100},
        "conversion_currency": {"type": "keyword"},
        "consent_analytics": {"type": "boolean"},
        "consent_advertising": {"type": "boolean"},
        "metadata": {"type": "flattened"},
        "metadata_text": {"type": "text", "analyzer": "standard"},
    },
}


def build_metadata_text(metadata: dict[str, Any]) -> str:
    """Concatenate only allowlisted metadata values for full-text analysis (FR-026).

    An allowlist rather than everything: analysing arbitrary customer values would index
    identifiers, tokens, and free text nobody intended to make searchable.
    """
    parts = [str(metadata[key]) for key in METADATA_TEXT_ALLOWLIST if metadata.get(key) is not None]
    return " ".join(parts)
