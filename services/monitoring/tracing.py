"""The tracing seam: one provider choice, made by the profile, not the network.

This is the SDK half of the split that lets ``@traced`` stay where it is.
``shared/observability.py`` imports only the OpenTelemetry **API** and resolves
its tracer through the global provider; this module owns the **SDK** — building a
provider, choosing an exporter, and installing it. Two things follow from that
split, and both are the point:

- **``shared/`` never imports ``services/``.** The dependency arrow stays
  pointing the right way. The API's default provider is a no-op, so a ``@traced``
  function in a process that never called :func:`configure_tracing` — a script, a
  unit test, an import — emits nothing and costs nothing.
- **No call site changed.** ~30 functions already carry ``@traced``. They gained
  real spans because the global provider was swapped underneath them, which is
  what the Stage 1 seam was for.

**The test profile cannot reach the collector.** Not "does not" — *cannot*.
:class:`OTLPTracerProvider` refuses to construct when the ``test`` profile is
active, before the endpoint is read, so no ordering of imports, fixtures or
monkeypatches turns a unit test into a network call. This is the same mechanism
as the Anthropic client (ADR 0009) and the Voyage client (ADR 0011), applied to
the third external hop. See ADR 0016.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, Sampler, TraceIdRatioBased
from shared.logging import get_logger
from shared.observability import traced
from shared.version import get_version

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.sdk.trace.export import SpanExporter
    from shared.config import Settings

_logger = get_logger("monitoring.tracing")

# The OTLP http/protobuf traces path, appended to the configured base endpoint.
# Spelled out rather than left to the exporter's env-var discovery, because the
# endpoint arrives as a `Settings` field like every other bit of config (ADR 0003)
# and the exporter must not read the environment behind that.
_TRACES_PATH = "/v1/traces"


@traced
def _resource(settings: Settings) -> Resource:
    """Identify this service on every span it emits.

    These are the only process-wide attributes attached to spans, and all four
    are static identity — the same values already on every structured log line
    (``shared/logging.py``). Nothing request-derived belongs here.
    """
    return Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": get_version(),
            "deployment.environment": settings.environment,
            "service.namespace": "production-llm-platform",
        }
    )


@traced
def _sampler(settings: Settings) -> Sampler:
    """Head sampling, inherited from the parent when there is one.

    ``ParentBased`` matters more than the ratio: without it a sampled parent can
    have an unsampled child, and a trace with holes in it is worse than no trace
    — it looks like the missing span never ran.
    """
    if settings.otel_traces_sample_ratio >= 1.0:
        return ParentBased(root=ALWAYS_ON)
    return ParentBased(root=TraceIdRatioBased(settings.otel_traces_sample_ratio))


class OTLPTracerProvider(TracerProvider):
    """The real provider. Batches spans to the OTel Collector over OTLP/HTTP.

    Cannot be constructed under the ``test`` profile — see the module docstring
    and ADR 0016.
    """

    def __init__(self, settings: Settings) -> None:
        if settings.is_test:
            raise RuntimeError(
                "OTLPTracerProvider must never be constructed under the 'test' profile: "
                "the suite is hermetic by construction and makes no network calls. "
                "Use build_tracer_provider(settings), which returns a LocalTracerProvider here."
            )
        if not settings.otel_exporter_otlp_endpoint:
            raise ValueError(
                "OTEL_EXPORTER_OTLP_ENDPOINT is not set; it is required to export spans"
            )
        super().__init__(resource=_resource(settings), sampler=_sampler(settings))
        endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + _TRACES_PATH
        # Batched, not simple: a span must never make the request that produced it
        # wait on the collector. A dead collector costs dropped spans and a log
        # line from the SDK — never a failed chat request.
        self.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=endpoint,
                    timeout=int(settings.otel_export_timeout_seconds),
                )
            )
        )


class LocalTracerProvider(TracerProvider):
    """What ``test`` runs: spans are real, nothing leaves the process.

    Real code, not a stub — the same reasoning as ``ScriptedLLMClient`` (ADR 0009)
    and ``HashingEmbeddingsClient`` (ADR 0011). Spans are genuinely started,
    nested, attributed and ended, so the decorator, the context propagation and
    the span shape are all exercised for real; only the export hop is absent.

    ``exporter`` is how a test collects them — pass an ``InMemorySpanExporter``
    and assert on what came out. Left unset (the default, and what the ``test``
    profile's own wiring uses), spans are started and dropped: no processor, no
    buffer, no socket.
    """

    def __init__(self, settings: Settings, *, exporter: SpanExporter | None = None) -> None:
        super().__init__(resource=_resource(settings), sampler=_sampler(settings))
        if exporter is not None:
            # SimpleSpanProcessor, not Batch: a test that asserts on a span must
            # not have to wait for a batch timer to flush it.
            self.add_span_processor(SimpleSpanProcessor(exporter))


@traced
def build_tracer_provider(settings: Settings) -> TracerProvider:
    """Return the tracer provider the active profile is allowed to use.

    This is the enforcement point named in ADR 0016, and it is keyed on the
    **profile**, not on whether a collector happens to answer — the same rule as
    ADR 0009/0011. A developer with a collector running locally and a real
    endpoint exported still gets CI's provider when the profile is ``test``.

    It is belt and braces with :class:`OTLPTracerProvider`'s own refusal: a test
    that reaches past this factory still cannot dial out.

    Outside ``test``, an unset endpoint also yields the local provider — the
    datastore rule (ADR 0005): not configured means never dialled. Tracing is not
    worth a boot failure, so this logs the fact rather than raising.
    """
    if settings.is_test:
        _logger.info(
            "tracing.provider_selected", extra={"provider": "local", "reason": "test profile"}
        )
        return LocalTracerProvider(settings)
    if not settings.otel_exporter_otlp_endpoint:
        _logger.warning(
            "tracing.provider_selected",
            extra={"provider": "local", "reason": "OTEL_EXPORTER_OTLP_ENDPOINT not set"},
        )
        return LocalTracerProvider(settings)
    _logger.info(
        "tracing.provider_selected",
        extra={
            "provider": "otlp",
            "endpoint": settings.otel_exporter_otlp_endpoint,
            "sample_ratio": settings.otel_traces_sample_ratio,
        },
    )
    return OTLPTracerProvider(settings)


@traced
def configure_tracing(settings: Settings, app: FastAPI) -> TracerProvider:
    """Install the profile's tracer provider globally and instrument ``app``.

    Called once from the lifespan. Everything downstream — ``@traced``, the
    FastAPI instrumentation, any library that speaks OTel — resolves through the
    global provider, which is why this is the only place the SDK is named.

    Both halves sit behind :func:`build_tracer_provider`, so ``test`` gets the
    local provider and the instrumentation has nothing to export through. The
    instrumentation itself is safe to install under any profile: it only wraps
    the ASGI app, and where its spans go is the provider's business, not its own.
    """
    provider = build_tracer_provider(settings)
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        # /health is a liveness probe on a 10s Docker healthcheck and /metrics is
        # a 15s Prometheus scrape. Tracing them buys nothing and would bury the
        # request traces anyone actually opens Tempo to read.
        excluded_urls="health,metrics",
    )
    _logger.info(
        "tracing.configured",
        extra={"provider": type(provider).__name__, "instrumented": "fastapi"},
    )
    return provider


@traced
def shutdown_tracing(provider: TracerProvider) -> None:
    """Flush and stop the provider on the way out.

    Without this the last batch dies with the process, which is exactly the batch
    covering whatever was happening when someone killed it. Never raises: a
    failure to flush traces must not turn a clean shutdown into a stack trace.
    """
    try:
        provider.shutdown()
    except Exception as exc:
        _logger.warning("tracing.shutdown_failed", exc_info=exc)
