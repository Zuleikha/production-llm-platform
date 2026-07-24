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

from services.agents.tools import ToolRegistry
from services.api.completions import CompletionEngine, OrchestratorEngine
from services.api.errors import register_exception_handlers
from services.api.middleware import RequestContextMiddleware
from services.api.routes import chat, health, meta
from services.monitoring.tracing import configure_tracing, shutdown_tracing
from services.orchestrator.base import AgentOrchestrator
from services.orchestrator.conversations import build_conversation_store
from services.orchestrator.graph import AgentGraph
from services.orchestrator.llm import build_llm_client, build_resilient_llm_client
from services.retrieval.retriever import build_retriever
from services.retrieval.tool import DocumentSearch
from services.security.auth import build_auth_provider
from services.security.guardrails import InjectionPatternGuardrail, UserInputGuardrail
from services.security.rate_limit import build_rate_limiter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_logger = get_logger("api.app")


@traced
def _build_tools(settings: Settings, registry: DatastoreRegistry) -> ToolRegistry:
    """The offline tools, plus document retrieval when there is a Qdrant to search.

    This is where the retrieval tool joins the set the agent may call (Stage 4).
    It is composed here rather than inside ``ToolRegistry.default()`` because a
    tool needing a live datastore cannot be a default — and because
    ``services.agents`` deliberately does not know retrieval exists.
    """
    tools = ToolRegistry.default()
    retriever = build_retriever(settings, registry.qdrant_client)
    if retriever is None:
        _logger.info("retrieval.tool_disabled", extra={"reason": "qdrant not available"})
        return tools
    # Stage 8: a heuristic injection screen over retrieved excerpts (ADR 0019).
    # Defense-in-depth telemetry only — it never drops an excerpt, and the ADR
    # 0014 nonce fence remains the load-bearing mitigation.
    screen = InjectionPatternGuardrail() if settings.retrieval_guardrail_enabled else None
    _logger.info("retrieval.tool_enabled", extra={"collection": settings.qdrant_collection})
    return tools.with_tools(DocumentSearch(retriever, screen=screen))


@traced
def _build_engine(settings: Settings, registry: DatastoreRegistry) -> OrchestratorEngine:
    """Assemble the agent stack behind the completion seam.

    Called after ``registry.startup()``: the conversation store needs live
    pools, and a store built before them would hold ``None`` forever. The same
    now goes for the retrieval tool, which needs a live Qdrant client.
    """
    # The circuit breaker sits between the graph and the real client (ADR 0020):
    # an Anthropic outage fails fast with 503 rather than tying every request up
    # in the SDK's own timeout. Harmless under `test`, where the wrapped client is
    # the scripted double that never raises a provider error.
    llm = build_resilient_llm_client(settings, build_llm_client(settings))
    graph = AgentGraph(
        llm,
        tools=_build_tools(settings, registry),
        max_steps=settings.agent_max_steps,
        max_tokens=settings.anthropic_max_tokens,
        context_window_messages=settings.context_window_messages,
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
        # Before anything else worth tracing happens. Which provider this
        # installs is decided by the profile, not by whether a collector answers
        # — under `test` it cannot be the exporting one (ADR 0016).
        app.state.tracer_provider = configure_tracing(settings, app)
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
        # Last: flushes the batch covering the shutdown itself.
        shutdown_tracing(app.state.tracer_provider)
        _logger.info("service.shutdown", extra={"environment": settings.environment})

    app = FastAPI(
        title="production-llm-platform :: api",
        version=get_version(),
        summary="Agent-backed chat API — health, readiness, version, metrics, completions.",
        # NEVER wire settings.debug here. FastAPI passes `debug` to Starlette's
        # ServerErrorMiddleware, and with debug=True that middleware returns a raw
        # traceback and BYPASSES our registered catch-all Exception handler — so an
        # unhandled 500 would leak internals in the response body, violating the
        # standing "500s never leak internals" rule (CLAUDE.md) in the dev profile
        # (where DEBUG=true). The full trace still goes to the server log via
        # unhandled_exception_handler. settings.debug remains the uvicorn --reload
        # switch in services/api/__main__.py, which is unaffected by this.
        debug=False,
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Built before startup so the dependency is resolvable even if a probe runs
    # before the lifespan has finished connecting.
    app.state.datastores = DatastoreRegistry.from_settings(settings)
    # --- Stage 8 security components (ADR 0019) ---
    # All local logic with no paid external hop, so they are constructed here
    # (before the lifespan) and work identically under every profile. The rate
    # limiter reads registry.redis_client at call time, so it tolerates Redis
    # connecting later in the lifespan (or never — it fails open).
    app.state.auth_provider = build_auth_provider(settings)
    app.state.rate_limiter = build_rate_limiter(settings, app.state.datastores)
    app.state.input_guardrail = UserInputGuardrail() if settings.input_guardrail_enabled else None
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
