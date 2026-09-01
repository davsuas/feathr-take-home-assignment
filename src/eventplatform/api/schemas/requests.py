"""Request schemas.

``extra="forbid"`` on purpose: silently accepting an unknown field means a client typo
becomes data loss the sender never learns about.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from eventplatform.domain.event_type import Channel, EventType


class ConversionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: str | None = Field(default=None, max_length=128)
    value: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Optional, and ``None`` is meaningful: absent is not the same as false (FR-043).
    analytics: bool | None = None
    advertising: bool | None = None


class EventSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Supply this to make your own retries idempotent. Omit it and the platform "
            "generates one, which means a retry of this submission becomes a SECOND event."
        ),
    )
    event_type: EventType
    occurred_at: datetime
    anonymous_id: str | None = Field(default=None, max_length=128)
    contact_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    campaign_id: str | None = Field(default=None, max_length=128)
    channel: Channel | None = None
    source_url: str | None = Field(default=None, max_length=2048)
    conversion: ConversionIn | None = None
    consent: ConsentIn | None = None
    metadata: dict[str, Any] | None = None
