"""Idempotency decision (R4).

Under at-least-once delivery a duplicate is a normal outcome, not an error. That is why
persistence returns an outcome rather than raising: the worker acknowledges all three
cases (the event is durably resolved in every one) and counts them separately.
"""

from __future__ import annotations

from enum import StrEnum


class InsertOutcome(StrEnum):
    INSERTED = "inserted"
    DUPLICATE_SUPPRESSED = "duplicate_suppressed"
    DUPLICATE_CONFLICT = "duplicate_conflict"


def classify_duplicate(*, incoming_hash: str, stored_hash: str | None) -> InsertOutcome:
    """A duplicate key with a matching content hash is the same event arriving twice.
    A mismatch means two different events claimed one id - the first stays (FR-014)."""
    if stored_hash is not None and stored_hash == incoming_hash:
        return InsertOutcome.DUPLICATE_SUPPRESSED
    return InsertOutcome.DUPLICATE_CONFLICT
