"""Liveness and readiness endpoints.

- ``/health`` — liveness: the process is up and serving. Answers ``ok`` whenever
  the app can respond, and **never touches a datastore**. An orchestrator uses
  this to decide whether to restart the container, so making it depend on
  Postgres would turn a database blip into a pod restart storm.
- ``/ready`` — readiness: the service can serve traffic *right now*. This does
  probe every configured datastore, and returns 503 when any of them is
  unreachable, so traffic is routed away without killing the container.

The split is the whole point: liveness answers "is this process alive?",
readiness answers "should it get requests?". See ADR 0005.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from shared.config import Settings, get_settings
from shared.datastores import DatastoreRegistry, ProbeStatus
from shared.observability import traced
from shared.version import get_version

router = APIRouter(tags=["health"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_datastores(request: Request) -> DatastoreRegistry:
    """Return the registry the lifespan attached to the app.

    A dependency rather than a direct ``app.state`` read so tests can swap in
    fakes via ``app.dependency_overrides``.
    """
    registry = request.app.state.datastores
    if not isinstance(registry, DatastoreRegistry):  # pragma: no cover - lifespan guarantees type
        raise RuntimeError("datastore registry is not initialised")
    return registry


DatastoresDep = Annotated[DatastoreRegistry, Depends(get_datastores)]


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: str
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    """Readiness payload; ``checks`` maps dependency name -> status."""

    status: str
    checks: dict[str, ProbeStatus]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
@traced
async def health(settings: SettingsDep) -> HealthResponse:
    """Return liveness status and service identity. Probes nothing."""
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=get_version(),
        environment=settings.environment,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
@traced
async def ready(datastores: DatastoresDep, response: Response) -> ReadinessResponse:
    """Probe every configured datastore concurrently and report readiness.

    An unconfigured datastore reports ``not_configured`` and does not block
    readiness — under ``prod`` a missing URL is already rejected at boot by
    ``Settings``, so this cannot mask a misconfigured production service.
    """
    checks = await datastores.probe()
    unavailable = [name for name, status in checks.items() if status == "unavailable"]
    if unavailable:
        response.status_code = 503
        return ReadinessResponse(status="not_ready", checks=checks)
    return ReadinessResponse(status="ready", checks=checks)
