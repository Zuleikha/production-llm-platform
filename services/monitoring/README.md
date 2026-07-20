# monitoring

> **Status: implemented in Stage 5 (observability).**

Owns the tracing seam: which OpenTelemetry tracer provider the active profile is
allowed to construct, and the wiring that installs it.

## What this service does *not* own

- **`@traced`** lives in `shared/observability.py` and stays there. It imports the
  OTel **API** only and resolves through the global provider, so `shared/` never
  imports `services/` and a process that never configured tracing emits nothing.
- **Metrics.** `/metrics` is `prometheus-client`, scraped directly by Prometheus,
  and Stage 5 deliberately did **not** route it through the collector — one
  metrics pipeline, not two competing ones (ADR 0016).

## Contract

| Name | Kind | Meaning |
|------|------|---------|
| `build_tracer_provider` | `(Settings) -> TracerProvider` | **The seam.** Profile-keyed: `test` can only get `LocalTracerProvider`. |
| `OTLPTracerProvider` | `TracerProvider` | The real one. Batches spans to the collector over OTLP/HTTP. **Refuses to construct under `test`.** |
| `LocalTracerProvider` | `TracerProvider` | Real spans, no export hop. What `test` runs; takes an `InMemorySpanExporter` when a test wants to assert on spans. |
| `configure_tracing` | `(Settings, FastAPI) -> TracerProvider` | Installs the provider globally + instruments FastAPI. Called once, from the lifespan. |
| `shutdown_tracing` | `(TracerProvider) -> None` | Flushes the final batch. Never raises. |
| `SpanExporter` | re-export | OpenTelemetry's. The Stage 1 in-house sketch was retired — see `base.py`. |

## The guard

`test` cannot reach the collector — *cannot*, not "does not". Two independent
mechanisms, the same shape as the Anthropic (ADR 0009) and Voyage (ADR 0011)
guards:

1. `build_tracer_provider(settings)` returns `LocalTracerProvider` under `test`
   and never reaches the real constructor.
2. `OTLPTracerProvider.__init__` **raises** under `test`, before the endpoint is
   read. This is the load-bearing one: code that reaches past the factory still
   cannot dial out.

Keyed on the **profile**, not on whether a collector answers. A developer with a
collector running and an endpoint exported still gets CI's provider.

Outside `test`, an unset `OTEL_EXPORTER_OTLP_ENDPOINT` also yields the local
provider — the datastore rule (ADR 0005): not configured means never dialled.
Tracing is not worth a boot failure, so that logs a warning rather than raising.

## Modules

| Module | Does |
|--------|------|
| `tracing.py` | The seam, both providers, resource/sampler construction, FastAPI instrumentation, shutdown. |
| `base.py` | Records why the Stage 1 `SpanExporter` ABC was retired rather than built. |

See ADR 0016 for the stack decision (Tempo · collector · Grafana alerting) and
what was deliberately left out.
