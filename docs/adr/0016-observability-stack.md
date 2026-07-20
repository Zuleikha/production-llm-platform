# ADR 0016 — The observability stack: OTel traces → Collector → Tempo, Grafana-native alerting

- **Status:** Accepted
- **Date:** 2026-07-18
- **Stage:** 5 (Observability)
- **Builds on:** ADR 0009/0011 (hermetic-by-construction provider seams), ADR
  0005 (not configured means never dialled), ADR 0010 (pre-rendered diagrams).

## Context

Since Stage 1 every application function has carried `@traced`, and until now
that decorator did exactly one thing: emit a structured DEBUG log on enter, exit
and error. The seam was always meant to grow a real distributed-tracing backend
without touching its ~30 call sites — that was the point of putting it behind a
decorator. Stage 5 is where it grows one.

Three things had to be decided together, because they are one stack, not three
independent picks:

1. **A trace backend and how spans reach it.**
2. **Where alerting lives.**
3. **Whether metrics also move onto the new pipeline.**

The repo already provisions Prometheus and Grafana as code (Stage 1). That
existing shape constrains the sensible answer more than any abstract comparison
of backends would.

## Decision

### 1. Trace backend: Grafana Tempo, fed by an OpenTelemetry Collector

`@traced` now emits a real OTel span (start/end, function qualname as the span
name, exception recorded on error) *in addition to* its DEBUG log — additive, not
a replacement. FastAPI auto-instrumentation (`opentelemetry-instrumentation-fastapi`)
is wired in the lifespan so each request is a root span the `@traced` spans nest
under.

Spans leave the API over **OTLP/HTTP** to a new **OTel Collector** container,
which batches and forwards them over OTLP/gRPC to a new **Tempo** container.
Grafana reads Tempo as a native datasource, provisioned as code exactly like the
existing Prometheus one.

```
api --OTLP/http:4318--> otel-collector --OTLP/grpc:4317--> tempo <-- grafana
```

**Why Tempo, not Jaeger:** Grafana and Prometheus are already provisioned as code
here, and Tempo is a first-class Grafana datasource using the same provisioning
mechanism. Choosing it adds one container *family* (collector + Tempo) that
extends the stack already present, rather than standing up Jaeger's own UI and
storage as a second, unrelated observability island.

**Why a Collector hop the API could skip.** The API could write to Tempo
directly. The collector earns its place as the single point where sampling,
batching and (later) redaction apply to *every* trace producer at once, and as
the seam that lets the trace backend change without touching a line of
application config. It is the tracing analogue of a datastore registry: one place
that owns the outbound decision.

**Why OTLP/HTTP, not gRPC, from the API.** Not a dependency-weight argument —
`grpcio` is already in the tree via `qdrant-client`. http/protobuf keeps the
collector's `:4318` receiver `curl`-able when a trace goes missing, and it avoids
a second package contending with `qdrant-client` over `grpcio`'s version range.
The collector listens on both protocols, so nothing downstream depends on the
choice.

### 2. Alerting: Grafana's built-in unified alerting, provisioned as code

Grafana 11 (already pinned) ships unified alerting natively. Rules live in YAML
under `infrastructure/docker/grafana/provisioning/alerting/`. **No Alertmanager
container** — one fewer moving part for the same outcome. Prometheus's own
`rule_files` stays empty by design.

Rules are **symptom-level only** — each fires on something a *caller* would
notice, never on an internal:

| Alert | Threshold | `for` | Severity | Why this number |
|-------|-----------|:-----:|----------|-----------------|
| Error rate | 5xx / total > **5%** | 5m | critical | Past 5% it is no longer one unlucky client; at any real volume a slice of traffic is broken. `clamp_min` on the denominator stops an idle service evaluating 0/0. The 5m `for` rides out a deploy blip or a datastore reconnect. |
| Chat p99 latency | > **30s** on `/v1/chat/completions` | 10m | warning | Scoped to the chat route so `/health` and `/metrics` don't dilute it. A grounded answer runs up to `AGENT_MAX_STEPS`(6) model calls each bounded by a 60s timeout, so seconds are legitimate; 30s means the worst request is burning half a single call's budget — outside normal even multi-step. Warning: slow is degradation, not outage. |
| `/ready` not_ready | 503 count > **0** | 2m | critical | Any occurrence — there is no healthy background level of `not_ready`. The 2m `for` rides out a restart or reconnect, which matters because startup deliberately never raises (ADR 0005). |

### 3. Metrics stay on Prometheus. The collector handles traces only.

The collector pipeline is **traces only**. The API keeps exposing Prometheus
metrics on `/metrics` and Prometheus keeps scraping it directly (Stage 1). A
second metrics pipeline through the collector would mean two sources of truth for
the same numbers, disagreeing at the edges. **This is a deliberate scope cut, not
an oversight** — stated explicitly so a later reader does not "finish" it. It is
also why the Tempo datasource ships with `nodeGraph`/`serviceMap` *disabled*:
those are driven by the collector's spanmetrics/servicegraph connectors, i.e. the
very metrics pipeline this ADR declined, and enabling them against series nothing
produces would render "No data" forever.

### Hermetic by construction — the third vendor

Same mechanism as the Anthropic client (ADR 0009) and the Voyage client (ADR
0011), applied to the collector. Tracer-provider setup goes through a seam,
`build_tracer_provider(settings)`, keyed on the **profile**, not on whether a
collector answers:

- **`test`** gets a `LocalTracerProvider` — spans are real (started, nested,
  attributed, ended) but never leave the process. `OTLPTracerProvider` *raises*
  in its constructor under `test`, before the endpoint is read, so no ordering of
  imports, fixtures or monkeypatches turns a unit test into a network call.
- **`dev`/`prod`** with an endpoint set get the exporting `OTLPTracerProvider`.
- **`dev`/`prod`** with **no** endpoint get the local provider and a log line —
  the datastore rule (ADR 0005): not configured means never dialled.

**Tracing is deliberately not a `prod` boot requirement** — unlike the datastore
URLs and the API keys. A trace backend is not worth refusing to serve traffic
over: an unset `OTEL_EXPORTER_OTLP_ENDPOINT` under `prod` yields a service that
runs untraced and says so in its startup log, not a crash. A dead collector at
runtime costs dropped spans (the SDK's `BatchSpanProcessor` drops them and logs),
never a failed chat request.

### Span hygiene

The logging rule — never PII, secrets, tokens, queries or excerpts — applies
identically to span attributes, because a span goes to Tempo, one more place data
can leak. Every span `@traced` emits carries **exactly two attributes**, both
read off the *function object at decoration time*, never from a call:

- `code.function` — the function's qualified name
- `code.namespace` — its module

There is structurally no code path from a wrapped call's arguments or return
value onto a span. On error, the exception is recorded as an event and the status
description is the exception **type name only** — the message is confined to the
recorded event (which the DEBUG log beside it already carries), never repeated as
a searchable status. `SpanExporter`'s own default handler would overwrite that
status with `f"{type}: {message}"`, so the decorator sets
`record_exception=False, set_status_on_exception=False` and owns both itself.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **Jaeger for traces** | Second, unrelated UI + storage island. Tempo is a native Grafana datasource reusing the provisioning already here. |
| **API writes to Tempo directly, no collector** | Loses the one place sampling/batching/redaction apply to every producer, and couples app config to the backend choice. |
| **Alertmanager container** | Grafana 11 ships unified alerting natively; a separate container is a moving part for no additional capability. |
| **Metrics through the collector too** | Two sources of truth for the same numbers. Prometheus already scrapes `/metrics` and works. Explicit scope cut. |
| **OTLP/gRPC from the API** | http/protobuf keeps `:4318` curl-able for debugging and avoids a second `grpcio` version constraint; the collector accepts both anyway. |
| **A homegrown `SpanExporter` ABC** (the Stage 1 stub) | OTel's SDK already defines a better contract — a batch of `ReadableSpan`s with a retryable result, implemented by every exporter worth having. The stub's `(name, attributes) -> None` cannot express a trace. Retired; the name now re-exports OTel's. |

## Consequences

**Positive**

- Every `@traced` function and every HTTP request is now a real span in Tempo,
  searchable and correlated to the Prometheus RED metrics via `tracesToMetrics`.
- The suite stays hermetic, free and key-free — the third external hop cannot be
  dialled under `test`, by construction, not by convention.
- The whole stack is code: dashboards, datasources and alert rules are all
  provisioned from files in git.

**Negative / accepted trade-offs**

- **No service map / node graph** until (if ever) a metrics-from-traces pipeline
  is built — deferred with metrics export.
- **The `/ready` alert is armed but quiet locally.** It counts what `/ready`
  *told a caller*, and nothing in the compose stack polls `/ready` (the Docker
  healthcheck hits `/health`, which by design never probes a dependency). In
  Stage 7 the kubelet's readiness probe polls it and the rule works as written.
  Recorded, not papered over.
- **Local-development shape only.** Single-binary Tempo on a local volume, no
  TLS on the collector→Tempo hop, generous sampling (ratio 1.0). A real
  deployment splits Tempo's components, uses object storage, and tunes sampling —
  that is Stage 7 (Kubernetes) and Stage 9 (reliability), not this ADR.
