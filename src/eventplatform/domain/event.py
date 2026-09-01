"""The canonical event envelope (data-model.md).

An event is an immutable record of something that happened. Nothing in this module
offers a way to change one after construction: the dataclasses are frozen, and the
repository port deliberately exposes no update or delete for event content.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = 1


class ProjectionStatus(StrEnum):
    PENDING = "pending"
    INDEXED = "indexed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Identity:
    """Anonymous and known identity are independent (FR-007). Neither implies the other."""

    anonymous_id: str | None = None
    contact_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class Conversion:
    goal_id: str | None = None
    value: float | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class Attribution:
    campaign_id: str | None = None
    channel: str | None = None
    source_url: str | None = None
    conversion: Conversion | None = None


@dataclass(frozen=True, slots=True)
class Consent:
    """Absent is distinct from false (FR-043). ``None`` means the sender said nothing."""

    analytics: bool | None = None
    advertising: bool | None = None


@dataclass(frozen=True, slots=True)
class Source:
    """Provenance. Deliberately holds no raw IP - only a salted hash (FR-036)."""

    producer: str = "http-api"
    ip_hash: str | None = None
    user_agent_family: str | None = None


@dataclass(frozen=True, slots=True)
class Projection:
    """Projection state lives on the event document, which is what lets a single-document
    write keep the event and its indexing status consistent with no transaction (R5)."""

    status: ProjectionStatus = ProjectionStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Event:
    tenant_id: str
    event_id: str
    event_type: str
    occurred_at: datetime
    received_at: datetime
    identity: Identity = field(default_factory=Identity)
    attribution: Attribution = field(default_factory=Attribution)
    consent: Consent = field(default_factory=Consent)
    source: Source = field(default_factory=Source)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    projection: Projection = field(default_factory=Projection)

    def content_hash(self) -> str:
        """Stable hash of the meaningful content, used to tell a benign duplicate from a
        conflicting one (R4, FR-014).

        Deliberately excludes ``received_at`` and ``projection``: both differ between two
        deliveries of the same event, so including them would make every redelivery look
        like a conflict.
        """
        payload = {
            "tenant_id": self.tenant_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "identity": asdict(self.identity),
            "attribution": asdict(self.attribution),
            "consent": asdict(self.consent),
            "metadata": self.metadata,
            "schema_version": self.schema_version,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()
