"""Domain error to HTTP mapping (RFC 9457 problem details).

Kept in one table so the mapping is reviewable in a single place, rather than scattered
as ad-hoc ``raise HTTPException`` calls that drift apart over time.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from eventplatform.domain.errors import (
    CacheUnavailable,
    ConflictingDuplicate,
    DeadLetterNotFound,
    DomainError,
    QueueAtCapacity,
    SearchUnavailable,
    ValidationError,
)
from eventplatform.infrastructure.observability.correlation import request_id_var

_STATUS: dict[type[DomainError], tuple[int, str]] = {
    ValidationError: (422, "validation-failed"),
    ConflictingDuplicate: (409, "conflicting-duplicate"),
    QueueAtCapacity: (503, "queue-at-capacity"),
    SearchUnavailable: (503, "search-unavailable"),
    CacheUnavailable: (503, "cache-unavailable"),
    DeadLetterNotFound: (404, "not-found"),
}


def problem(
    status: int, kind: str, title: str, *, detail: str | None = None, field: str | None = None
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"https://eventplatform.local/problems/{kind}",
        "title": title,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    if field:
        body["field"] = field
    trace_id = request_id_var.get()
    if trace_id:
        body["trace_id"] = trace_id
    headers = {"Retry-After": "5"} if status == 503 else None
    return JSONResponse(
        status_code=status, content=body, media_type="application/problem+json", headers=headers
    )


async def domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    status, kind = _STATUS.get(type(exc), (500, "internal-error"))
    field = getattr(exc, "field", None)
    return problem(status, kind, kind.replace("-", " ").title(), detail=str(exc), field=field)
