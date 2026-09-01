"""Health, readiness, and metrics.

Liveness and readiness are separate on purpose (FR-041): a process that is alive but
cannot reach MongoDB should stop receiving traffic without being restarted, and a single
opaque status cannot express that difference.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from eventplatform.api.dependencies import ContainerDep
from eventplatform.domain.identity import TenantContext

router = APIRouter(tags=["ops"])


@router.get("/healthz", summary="Liveness - is the process alive")
async def healthz() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/readyz", summary="Readiness - can the service accept work")
async def readyz(container: ContainerDep, response: Response) -> dict[str, object]:
    checks: dict[str, str] = {}

    try:
        await container.mongo_client.admin.command("ping")
        checks["mongodb"] = "ok"
    except Exception as exc:
        checks["mongodb"] = f"unavailable: {type(exc).__name__}"

    try:
        await container.cache.get(_ops_tenant(), f"v1:{_ops_tenant().tenant_id}:stats:rt:readiness")
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"unavailable: {type(exc).__name__}"

    try:
        if container.search is not None:
            await container.search.ensure_mapping()
        checks["elasticsearch"] = "ok"
    except Exception as exc:
        checks["elasticsearch"] = f"unavailable: {type(exc).__name__}"

    # MongoDB is canonical, so its absence means the platform cannot accept work.
    # Redis and Elasticsearch are derived: degraded, not unready (Principle IV).
    ready = checks["mongodb"] == "ok"
    response.status_code = 200 if ready else 503
    return {"status": "ready" if ready else "not-ready", "checks": checks}


@router.get("/metrics", summary="Operational signals")
async def metrics(container: ContainerDep) -> Response:
    return Response(content=container.metrics.render(), media_type="text/plain; version=0.0.4")


def _ops_tenant() -> TenantContext:
    """A synthetic context for the readiness probe only. It owns no data - it exists so
    the probe exercises the same tenant-scoped cache path a real request takes."""
    return TenantContext(tenant_id="_ops", credential_hash="_ops")
