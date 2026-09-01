"""FastAPI request-scoped dependencies.

Tenant resolution is the important part: the credential is hashed and exchanged for a
``TenantContext`` here, and that context is then a required argument on every port call.
A caller cannot name its own tenant anywhere in the request (FR-032, D1).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, Request

from eventplatform.api.errors import problem
from eventplatform.composition import Container, build_container
from eventplatform.domain.identity import TenantContext
from eventplatform.infrastructure.persistence.mongo.tenant_directory import hash_credential

__all__ = [
    "Container",
    "ContainerDep",
    "TenantDep",
    "Unauthenticated",
    "build_container",
    "get_container",
    "resolve_tenant",
    "unauthenticated_handler",
]


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


class Unauthenticated(Exception):
    """Raised for absent, unknown, and revoked credentials alike (FR-034)."""


async def resolve_tenant(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> TenantContext:
    container = get_container(request)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthenticated
    token = authorization.split(" ", 1)[1].strip()
    tenant = await container.tenants.resolve(hash_credential(token))
    if tenant is None:
        # Deliberately identical to the missing-credential case: any difference here,
        # including timing-visible branching, lets a caller enumerate valid keys.
        raise Unauthenticated
    return tenant


async def unauthenticated_handler(_request: Request, _exc: Exception) -> Any:
    return problem(
        401, "unauthenticated", "Unauthenticated", detail="Invalid or missing credential"
    )


TenantDep = Annotated[TenantContext, Depends(resolve_tenant)]
ContainerDep = Annotated[Container, Depends(get_container)]
