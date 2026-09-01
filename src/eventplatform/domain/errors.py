"""Domain errors.

These are the only failure vocabulary the application layer speaks. Adapters translate
driver-specific exceptions into these before they cross a port boundary, so a use case
never has to know that Redis raised ``ConnectionError`` or that Elasticsearch timed out.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base for every error the domain raises."""


class ValidationError(DomainError):
    """Input failed a domain rule. Carries the offending field so the API can name it."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class ConflictingDuplicate(DomainError):
    """Same (tenant_id, event_id) arrived with different content. First write wins."""

    def __init__(self, event_id: str) -> None:
        super().__init__(f"event {event_id} already exists with different content")
        self.event_id = event_id


class QueueAtCapacity(DomainError):
    """The queue cannot safely accept more work. The API must reject, never discard."""


class CacheUnavailable(DomainError):
    """The cache is unreachable. Callers MUST degrade rather than fail (FR-030)."""


class SearchUnavailable(DomainError):
    """The search index is unreachable. Canonical reads and writes are unaffected."""


class DeadLetterNotFound(DomainError):
    """No dead letter with that id for this tenant."""
