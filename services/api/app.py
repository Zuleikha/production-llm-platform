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
from shared.datastores import DatastoreRegistry
from shared.logging import get_logger, setup_logging
from shared.migrations import apply_pending
from shared.observability import traced
from shared.version import get_version

from services.api.completions import CompletionEngine, OrchestratorEngine
from services.api.errors import register_exception_handlers
from services.api.middleware import RequestContextMiddleware
from services.api.routes import chat, health, meta
from services.orchestrator.base import AgentOrchestrator
from services.orchestrator.conversations import build_conversation_store
from services.orchestrator.graph import AgentGraph
from services.orchestrator.llm import build_llm_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_logger = get_logger("api.app")


@traced
def _build_engine(settings: Settings, registry: DatastoreRegistry) -> OrchestratorEngine:
    """Assemble the agent stack behind the completion seam.

    Called after ``registry.startup()``: the conversation store needs live
    pools, and a store built before them would hold ``None`` forever.
    """
    graph = AgentGraph(
        build_llm_client(settings),
        max_steps=settings.agent_max_steps,
        max_tokens=settings.anthropic_max_tokens,
    )
    store = build_conversation_store(
        postgres_pool=registry.postgres_pool,
        redis=registry.redis_client,
        ttl_seconds=settings.conversation_cache_ttl_seconds,
    )
    return OrchestratorEngine(AgentOrchestrator(graph, store))


@traced
def create_app(
    settings: Settings | None = None, *, engine: CompletionEngine | None = None
) -> FastAPI:
    """Build and configure a FastAPI application instance.

    ``engine`` overrides the completion engine the app would otherwise build.
    Passing one also stops the lifespan from replacing it, which is what lets a
    caller pin a specific agent (a scripted model, say) and still exercise the
    real startup path.
    """
    settings = settings or get_settings()
    setup_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # --- startup hook ---
        _logger.info(
            "service.startup",
            extra={"version": get_version(), "environment": settings.environment},
        )
        # startup() never raises: an unreachable datastore must surface on
        # /ready, not crash-loop the container. See ADR 0005.
        await app.state.datastores.startup()
        await _migrate(app.state.datastores)
        # Rebuilt here so the conversation store gets the pools startup() just
        # opened — unless the caller supplied an engine, which must win.
        if not app.state.engine_overridden:
            app.state.engine = _build_engine(settings, app.state.datastores)
        yield
        # --- shutdown hook ---
        await app.state.datastores.shutdown()
        _logger.info("service.shutdown", extra={"environment": settings.environment})

    app = FastAPI(
        title="production-llm-platform :: api",
        version=get_version(),
        summary="Agent-backed chat API — health, readiness, version, metrics, completions.",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Built before startup so the dependency is resolvable even if a probe runs
    # before the lifespan has finished connecting.
    app.state.datastores = DatastoreRegistry.from_settings(settings)
    app.state.engine_overridden = engine is not None
    # A caller who never enters the lifespan still gets a working engine rather
    # than an AttributeError — it just has no persistence, because the pools are
    # not open yet.
    app.state.engine = engine or _build_engine(settings, app.state.datastores)

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(meta.router)
    app.include_router(chat.router)

    return app


async def _migrate(registry: DatastoreRegistry) -> None:
    """Apply pending migrations, if there is a database to apply them to.

    Never raises, for the same reason ``startup()`` does not (ADR 0005): a
    migration that cannot run must surface as an un-ready pod someone can read
    the logs of, not a crash loop that hides why. The service will fail its
    queries loudly on the first request instead.
    """
    pool = registry.postgres_pool
    if pool is None:
        return
    try:
        applied = await apply_pending(pool)
    except Exception as exc:
        _logger.error("migration.failed", exc_info=exc)
        return
    if applied:
        _logger.info("migration.complete", extra={"applied": applied})


# Module-level ASGI app for uvicorn (`services.api.app:app`).
app = create_app()
