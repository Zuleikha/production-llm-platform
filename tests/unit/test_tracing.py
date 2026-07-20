"""Tests for the tracing seam, the spans ``@traced`` emits, and span hygiene.

Three groups matter here.

:class:`TestTestProfileCannotReachTheCollector` pins the *mechanism* that keeps
the suite off the network, exactly as ``test_llm.py`` does for Anthropic (ADR
0009) and ``test_embeddings.py`` for Voyage (ADR 0011). If someone replaces the
guard with a convention — an autouse fixture, "we mock the exporter" — these fail.

:class:`TestTracedEmitsSpans` asserts the decorator's contract against an
``InMemorySpanExporter``. No collector, no Tempo, no socket: the spans are real,
they just never leave the process.

:class:`TestSpanHygiene` is the one that would catch a regression nobody notices
until it is in Tempo — that a span never carries a wrapped call's arguments,
return value, or any other runtime data (CLAUDE.md, ADR 0016).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from services.monitoring.tracing import (
    LocalTracerProvider,
    OTLPTracerProvider,
    build_tracer_provider,
    shutdown_tracing,
)
from shared.config import Settings
from shared.observability import traced

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from opentelemetry.sdk.trace import ReadableSpan

_ENDPOINT = "http://otel-collector:4318"


def _settings(**overrides: object) -> Settings:
    """Settings with the root .env ignored, so the suite is machine-independent."""
    defaults: dict[str, object] = {"_env_file": None, "environment": "test"}
    return Settings(**{**defaults, **overrides})  # type: ignore[arg-type]


def _dev_settings(**overrides: object) -> Settings:
    """A non-test profile with a collector endpoint configured."""
    return _settings(environment="dev", otel_exporter_otlp_endpoint=_ENDPOINT, **overrides)


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    """A ``LocalTracerProvider`` collecting spans in memory, installed globally.

    ``@traced`` resolves its tracer through the global provider, so a test that
    wants to see spans has to install one. Two things have to be reached past the
    public API for that to work per-test, both deliberate:

    - OTel only lets the global provider be *set* once (``set_tracer_provider``
      warns and no-ops after the first), so the global is swapped directly and
      restored on the way out rather than leaked into every later test.
    - ``shared.observability`` holds a module-level ``ProxyTracer`` that caches
      the first real tracer it resolves and never re-resolves. Left alone, the
      first test to emit a span would pin every later test to *its* (now
      shut-down) exporter. Resetting that cache forces re-resolution against the
      provider installed here.
    """
    import shared.observability as obs
    from opentelemetry import trace

    memory = InMemorySpanExporter()
    provider = LocalTracerProvider(_settings(), exporter=memory)
    # The RAW module global, not get_tracer_provider() — the latter returns the
    # proxy provider when the global is None, and restoring the proxy *into* the
    # global makes the ProxyTracer resolve to itself (infinite recursion).
    proxy: Any = obs._tracer
    previous = trace._TRACER_PROVIDER
    previous_real = proxy._real_tracer
    trace._TRACER_PROVIDER = provider
    proxy._real_tracer = None
    try:
        yield memory
    finally:
        trace._TRACER_PROVIDER = previous
        proxy._real_tracer = previous_real
        provider.shutdown()


def _finished(memory: InMemorySpanExporter) -> Sequence[ReadableSpan]:
    return memory.get_finished_spans()


class TestTestProfileCannotReachTheCollector:
    """The hermetic guard, third vendor. See ADR 0016."""

    def test_the_real_provider_refuses_to_construct_under_the_test_profile(self) -> None:
        settings = _settings(otel_exporter_otlp_endpoint=_ENDPOINT)
        with pytest.raises(RuntimeError, match="never be constructed under the 'test' profile"):
            OTLPTracerProvider(settings)

    def test_it_refuses_even_when_an_endpoint_is_configured(self) -> None:
        """The guard keys on the PROFILE, not on the endpoint's absence.

        A developer running the compose stack locally has a reachable collector
        and, quite reasonably, OTEL_EXPORTER_OTLP_ENDPOINT exported. They must
        still get exactly the provider CI gets.
        """
        settings = _settings(otel_exporter_otlp_endpoint="http://localhost:4318")
        with pytest.raises(RuntimeError, match="never be constructed under the 'test' profile"):
            OTLPTracerProvider(settings)

    def test_an_endpoint_in_the_os_environment_does_not_change_that(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case that distinguishes hermetic from merely-unconfigured."""
        from shared.config import get_settings

        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert settings.otel_exporter_otlp_endpoint == "http://localhost:4318"
            assert isinstance(build_tracer_provider(settings), LocalTracerProvider)
            with pytest.raises(RuntimeError, match="never be constructed under the 'test' profile"):
                OTLPTracerProvider(settings)
        finally:
            get_settings.cache_clear()

    def test_the_factory_returns_the_local_provider_under_the_test_profile(self) -> None:
        provider = build_tracer_provider(_settings(otel_exporter_otlp_endpoint=_ENDPOINT))
        assert isinstance(provider, LocalTracerProvider)
        assert not isinstance(provider, OTLPTracerProvider)


class TestBuildTracerProviderSelection:
    """Which provider each profile gets. Asserts the selection, never the network."""

    def test_dev_with_an_endpoint_selects_the_real_exporting_provider(self) -> None:
        provider = build_tracer_provider(_dev_settings())
        try:
            assert isinstance(provider, OTLPTracerProvider)
        finally:
            provider.shutdown()

    def test_prod_with_an_endpoint_selects_the_real_exporting_provider(self) -> None:
        settings = _settings(
            environment="prod",
            otel_exporter_otlp_endpoint=_ENDPOINT,
            database_url="postgresql://u:p@postgres:5432/db",
            redis_url="redis://redis:6379/0",
            qdrant_url="http://qdrant:6333",
            anthropic_api_key="sk-ant-not-real",
            voyage_api_key="pa-not-real",
        )
        provider = build_tracer_provider(settings)
        try:
            assert isinstance(provider, OTLPTracerProvider)
        finally:
            provider.shutdown()

    def test_dev_without_an_endpoint_falls_back_to_local_and_never_dials(self) -> None:
        """Not configured means never dialled — the datastore rule (ADR 0005)."""
        provider = build_tracer_provider(_settings(environment="dev"))
        assert isinstance(provider, LocalTracerProvider)

    def test_the_real_provider_refuses_an_empty_endpoint_rather_than_guessing(self) -> None:
        """Reached past the factory, it fails loud instead of defaulting to localhost."""
        with pytest.raises(ValueError, match="OTEL_EXPORTER_OTLP_ENDPOINT is not set"):
            OTLPTracerProvider(_settings(environment="dev"))

    def test_tracing_is_not_a_prod_boot_requirement(self) -> None:
        """Unlike the datastore URLs and the API keys — deliberately (ADR 0016).

        A trace backend is not worth refusing to serve traffic over. If this
        starts raising, someone has added OTEL_EXPORTER_OTLP_ENDPOINT to the prod
        validator's required list and changed that decision.
        """
        settings = Settings(
            _env_file=None,
            environment="prod",
            database_url="postgresql://u:p@postgres:5432/db",
            redis_url="redis://redis:6379/0",
            qdrant_url="http://qdrant:6333",
            anthropic_api_key="sk-ant-not-real",
            voyage_api_key="pa-not-real",
        )
        assert settings.otel_exporter_otlp_endpoint is None
        assert isinstance(build_tracer_provider(settings), LocalTracerProvider)


class TestResourceAndSampler:
    def test_spans_identify_the_service_that_emitted_them(
        self, exporter: InMemorySpanExporter
    ) -> None:
        @traced
        def work() -> None:
            return None

        work()
        resource = _finished(exporter)[0].resource
        assert resource.attributes["service.name"] == "api"
        assert resource.attributes["deployment.environment"] == "test"
        assert resource.attributes["service.namespace"] == "production-llm-platform"
        assert resource.attributes["service.version"]

    def test_a_ratio_below_one_still_builds(self) -> None:
        provider = build_tracer_provider(_settings(otel_traces_sample_ratio=0.1))
        assert isinstance(provider, LocalTracerProvider)

    def test_the_ratio_is_bounded_to_zero_and_one(self) -> None:
        with pytest.raises(ValueError):
            _settings(otel_traces_sample_ratio=1.5)
        with pytest.raises(ValueError):
            _settings(otel_traces_sample_ratio=-0.1)


class TestTracedEmitsSpans:
    """The decorator's span contract, against an in-memory exporter."""

    def test_a_sync_call_emits_one_span_named_for_the_function(
        self, exporter: InMemorySpanExporter
    ) -> None:
        @traced
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5

        spans = _finished(exporter)
        assert len(spans) == 1
        assert spans[0].name == (
            "TestTracedEmitsSpans."
            "test_a_sync_call_emits_one_span_named_for_the_function.<locals>.add"
        )
        assert spans[0].status.status_code is StatusCode.UNSET
        assert spans[0].kind is SpanKind.INTERNAL

    async def test_an_async_call_emits_one_span_too(self, exporter: InMemorySpanExporter) -> None:
        @traced
        async def fetch() -> str:
            return "done"

        assert await fetch() == "done"

        spans = _finished(exporter)
        assert len(spans) == 1
        assert spans[0].name.endswith("<locals>.fetch")

    def test_the_span_is_ended_and_has_a_real_duration(
        self, exporter: InMemorySpanExporter
    ) -> None:
        @traced
        def work() -> None:
            return None

        work()
        span = _finished(exporter)[0]
        assert span.end_time is not None
        assert span.start_time is not None
        assert span.end_time >= span.start_time

    def test_nested_traced_calls_nest_their_spans(self, exporter: InMemorySpanExporter) -> None:
        """The property a log line cannot give you, and the reason for the stage.

        Without a real parent/child relationship a trace is just a pile of
        events; this is what makes the Tempo span tree readable.
        """

        @traced
        def inner() -> int:
            return 1

        @traced
        def outer() -> int:
            return inner() + 1

        assert outer() == 2

        spans = {span.name.rsplit(".", 1)[-1]: span for span in _finished(exporter)}
        assert set(spans) == {"inner", "outer"}
        assert spans["inner"].parent is not None
        assert spans["outer"].parent is None
        assert spans["inner"].parent.span_id == spans["outer"].context.span_id
        assert spans["inner"].context.trace_id == spans["outer"].context.trace_id

    async def test_async_nesting_survives_the_await(self, exporter: InMemorySpanExporter) -> None:
        @traced
        async def inner() -> int:
            return 1

        @traced
        async def outer() -> int:
            return await inner() + 1

        assert await outer() == 2

        spans = {span.name.rsplit(".", 1)[-1]: span for span in _finished(exporter)}
        assert spans["inner"].parent is not None
        assert spans["inner"].parent.span_id == spans["outer"].context.span_id

    def test_a_raising_sync_call_records_the_exception_and_reraises(
        self, exporter: InMemorySpanExporter
    ) -> None:
        @traced
        def boom() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"):
            boom()

        span = _finished(exporter)[0]
        assert span.status.status_code is StatusCode.ERROR
        # The status description is the TYPE only — the message is confined to
        # the recorded event. See _record_error's docstring and ADR 0016.
        assert span.status.description == "ValueError"
        assert span.events
        event = span.events[0]
        assert event.name == "exception"
        assert event.attributes is not None
        assert event.attributes["exception.type"] == "ValueError"

    async def test_a_raising_async_call_records_the_exception_and_reraises(
        self, exporter: InMemorySpanExporter
    ) -> None:
        @traced
        async def boom() -> None:
            raise RuntimeError("async kaboom")

        with pytest.raises(RuntimeError, match="async kaboom"):
            await boom()

        span = _finished(exporter)[0]
        assert span.status.status_code is StatusCode.ERROR
        assert span.status.description == "RuntimeError"

    def test_the_span_ends_even_when_the_call_raises(self, exporter: InMemorySpanExporter) -> None:
        """A leaked, never-ended span is invisible in Tempo — worse than no span."""

        @traced
        def boom() -> None:
            raise ValueError("x")

        with pytest.raises(ValueError):
            boom()

        assert _finished(exporter)[0].end_time is not None

    def test_the_decorator_preserves_the_wrapped_function(
        self, exporter: InMemorySpanExporter
    ) -> None:
        """The Stage 1 contract: @traced is transparent to its call sites."""

        @traced
        def documented(a: int, b: int = 2) -> int:
            """Adds."""
            return a + b

        assert documented.__name__ == "documented"
        assert documented.__doc__ == "Adds."
        assert documented(1) == 3
        assert documented(1, b=5) == 6


class TestSpanHygiene:
    """No PII, secrets, queries or excerpts on a span — CLAUDE.md, ADR 0016.

    The decorator reads attributes off the *function object* at decoration time,
    so there is structurally no path from a call's data onto a span. These tests
    are what stop someone "improving" that by adding args to the attributes.
    """

    def test_span_attributes_are_static_code_identity_only(
        self, exporter: InMemorySpanExporter
    ) -> None:
        @traced
        def work(secret: str) -> str:
            return secret

        work("sk-ant-super-secret-value")

        span = _finished(exporter)[0]
        assert span.attributes is not None
        assert set(span.attributes) == {"code.function", "code.namespace"}
        assert span.attributes["code.namespace"] == __name__
        assert str(span.attributes["code.function"]).endswith("<locals>.work")

    def test_arguments_never_reach_the_span(self, exporter: InMemorySpanExporter) -> None:
        secret = "pa-voyage-key-do-not-log"
        query = "what did the user actually ask about"

        @traced
        def search(api_key: str, user_query: str, chunk: str) -> None:
            return None

        search(secret, query, "retrieved excerpt text from an untrusted document")

        rendered = _rendered(_finished(exporter)[0])
        assert secret not in rendered
        assert query not in rendered
        assert "untrusted document" not in rendered

    def test_return_values_never_reach_the_span(self, exporter: InMemorySpanExporter) -> None:
        @traced
        def answer() -> str:
            return "the model's answer, which may quote a retrieved document"

        answer()
        assert "retrieved document" not in _rendered(_finished(exporter)[0])

    async def test_the_async_path_has_the_same_hygiene(
        self, exporter: InMemorySpanExporter
    ) -> None:
        """Both wrappers, because there are two and only one gets read."""

        @traced
        async def search(user_query: str) -> None:
            return None

        await search("a query that must not be traced")

        span = _finished(exporter)[0]
        assert span.attributes is not None
        assert set(span.attributes) == {"code.function", "code.namespace"}
        assert "must not be traced" not in _rendered(span)


class TestShutdownTracing:
    def test_it_shuts_the_provider_down(self) -> None:
        provider = build_tracer_provider(_settings())
        shutdown_tracing(provider)

    def test_it_never_raises(self) -> None:
        """A failure to flush traces must not turn a clean shutdown into a crash."""

        class Exploding:
            def shutdown(self) -> None:
                raise RuntimeError("collector went away")

        shutdown_tracing(Exploding())  # type: ignore[arg-type]


def _rendered(span: ReadableSpan) -> str:
    """Everything about a span that would leave the process, as one string.

    Asserting on this rather than on `span.attributes` alone is deliberate: it
    catches a leak that lands in the name, an event or the status description,
    not just one that lands in an attribute.
    """
    parts: list[Any] = [span.name, span.status.description]
    parts.extend(f"{k}={v}" for k, v in (span.attributes or {}).items())
    for event in span.events:
        parts.append(event.name)
        parts.extend(f"{k}={v}" for k, v in (event.attributes or {}).items())
    return " ".join(str(part) for part in parts)
