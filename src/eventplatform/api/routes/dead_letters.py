"""Dead-letter inspection and replay."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from eventplatform.api.dependencies import ContainerDep, TenantDep
from eventplatform.api.schemas.responses import DeadLetterOut, DeadLetterPageOut

router = APIRouter(tags=["dead-letters"])


@router.get("/dead-letters", response_model=DeadLetterPageOut)
async def list_dead_letters(
    tenant: TenantDep,
    container: ContainerDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DeadLetterPageOut:
    letters, next_cursor = await container.dead_letters.list(tenant, cursor=cursor, limit=limit)
    return DeadLetterPageOut(
        items=[
            DeadLetterOut(
                event_id=letter.event_id,
                failure_reason=letter.failure_reason,
                failure_class=letter.failure_class,
                attempts=letter.attempts,
                first_failed_at=letter.first_failed_at,
                last_failed_at=letter.last_failed_at,
                replayed_at=letter.replayed_at,
            )
            for letter in letters
        ],
        next_cursor=next_cursor,
    )


@router.post("/dead-letters/{event_id}/replay", status_code=status.HTTP_202_ACCEPTED)
async def replay_dead_letter(
    event_id: str, tenant: TenantDep, container: ContainerDep
) -> dict[str, str]:
    replayed = await container.replay(tenant, event_id)
    return {"event_id": replayed, "status": "accepted"}
