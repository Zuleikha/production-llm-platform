"""FastAPI application factory for the ``api`` service.

Wires together configuration, structured logging, lifespan (startup/shutdown)
hooks, request-context middleware, global error handling, and the health /
readiness / version / metrics routes.

Run locally:      uv run uvicorn services.api.app:app --reload
Run as a module:  uv run python -m services.api
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from shared.config import Settings, get_settings
from shared.logging import get_logger, setup_logging
from shared.observability import traced
from shared.version import get_version

from services.api.errors import register_exception_handlers
from services.api.middleware import RequestContextMiddleware
from services.api.routes import health, meta

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_logger = get_logger("api.app")


@traced
def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure a FastAPI application instance."""
    settings = settings or get_settings()
    setup_logging(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # --- startup hook ---
        _logger.info(
            "service.startup",
            extra={"version": get_version(), "environment": settings.environment},
        )
        yield
        # --- shutdown hook ---
        _logger.info("service.shutdown", extra={"environment": settings.environment})

    app = FastAPI(
        title="production-llm-platform :: api",
        version=get_version(),
        summary="Stage 1 foundation service — health, readiness, version, metrics.",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(meta.router)

    return app


# Module-level ASGI app for uvicorn (`services.api.app:app`).
app = create_app()
