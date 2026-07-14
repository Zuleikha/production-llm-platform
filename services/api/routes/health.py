"""Liveness and readiness endpoints.

- ``/health`` — liveness: the process is up and serving. Always ``ok`` while the
  app can respond.
- ``/ready`` — readiness: the service is ready to accept traffic. In Stage 1
  there are no downstream dependencies wired in, so the ``checks`` map is empty
  and the service is always ready. Dependency probes (PostgreSQL, Redis,
  Qdrant) are **planned, not yet implemented** — added in Stage 2+.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from shared.config import Settings, get_settings
from shared.observability import traced
from shared.version import get_version

router = APIRouter(tags=["health"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: str
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    """Readiness payload; ``checks`` maps dependency name -> status."""

    status: str
    checks: dict[str, str]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
@traced
async def health(settings: SettingsDep) -> HealthResponse:
    """Return liveness status and service identity."""
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=get_version(),
        environment=settings.environment,
    )


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
@traced
async def ready() -> ReadinessResponse:
    """Return readiness status.

    No downstream dependencies are wired in Stage 1, so ``checks`` is empty and
    the service reports ``ready``.
    """
    checks: dict[str, str] = {}
    return ReadinessResponse(status="ready", checks=checks)
