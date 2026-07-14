"""Version and Prometheus metrics endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from shared.config import Settings, get_settings
from shared.observability import traced
from shared.version import get_version
from starlette.responses import Response

router = APIRouter(tags=["meta"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


class VersionResponse(BaseModel):
    """Service version and identity."""

    service: str
    version: str
    environment: str


@router.get("/version", response_model=VersionResponse, summary="Service version")
@traced
async def version(settings: SettingsDep) -> VersionResponse:
    """Return the running service version and identity."""
    return VersionResponse(
        service=settings.service_name,
        version=get_version(),
        environment=settings.environment,
    )


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_class=Response,
    responses={200: {"content": {CONTENT_TYPE_LATEST: {}}}},
)
@traced
async def metrics() -> Response:
    """Expose metrics in the Prometheus text exposition format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
