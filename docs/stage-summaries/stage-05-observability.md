# Stage 5 — Observability — self-report

- **Stage:** 5 of 10 (Observability)
- **Date:** 2026-07-18
- **Version:** `0.1.0` (unchanged)
- **Status:** Build complete, full gate green, full compose stack booted and a
  real trace confirmed in Tempo, both container profiles booted standalone.
  **Not committed** — awaiting independent manual verification, per the stage
  constraint.

This report states only what was directly run and observed this stage. It does
not restate prior-stage claims as re-verified.

---

## What was built, file by file

### New source

- **`services/monitoring/tracing.py`** — the SDK half of the tracing seam. Owns
  the provider/exporter choice so `shared/` never imports `services/`.
  - `_resource(settings)` — static service identity on every span (`service.name`,
    `service.version`, `deployment.environment`, `service.namespace`).
  - `_sampler(settings)` — `ParentBased` head sampling (a sampled parent never
    gets an unsampled child, which would look like a missing span).
  - `OTLPTracerProvider` — the real provider; `BatchSpanProcessor` → OTLP/HTTP.
    **Raises in its constructor under the `test` profile**, before the endpoint is
    read; also raises on an empty endpoint rather than defaulting to localhost.
  - `LocalTracerProvider` — real spans that never leave the process; optional
    `InMemorySpanExporter` for tests via `SimpleSpanProcessor`.
  - `build_tracer_provider(settings)` — the profile-keyed enforcement point:
    `test` → local; non-test without endpoint → local + a warning log; non-test
    with endpoint → OTLP.
  - `configure_tracing(settings, app)` — installs the provider globally and calls
    `FastAPIInstrumentor.instrument_app(..., excluded_urls="health,metrics")`.
  - `shutdown_tracing(provider)` — flushes on the way out; never raises.
- **`tests/unit/test_tracing.py`** — 27 tests (see the fix section below).

### Modified source

- **`shared/observability.py`** — `@traced` now emits a **real OTel span** in
  addition to its Stage 1 DEBUG log, for both the sync and async wrappers. Imports
  the OTel **API** only. Both wrappers pass `record_exception=False,
  set_status_on_exception=False` and record the error themselves via
  `_record_error`, so the status description is the exception **type name only**
  (the message stays in the recorded event). Span attributes are read off the
  function object at decoration time — `code.function`, `code.namespace`, nothing
  from the call.
- **`services/api/app.py`** — lifespan now calls `configure_tracing(settings, app)`
  first (before anything worth tracing), stores the provider on
  `app.state.tracer_provider`, and calls `shutdown_tracing(...)` last on shutdown.
- **`services/monitoring/base.py`** — the Stage 1 in-house `SpanExporter(ABC)` was
  **retired**; the module now re-exports OpenTelemetry's `SpanExporter`. Rationale
  in the file and ADR 0016: OTel's contract (a batch of `ReadableSpan`s, retryable
  result) already exists and is better than the `(name, attributes) -> None` sketch,
  which cannot express a trace. The seam this service actually owns is
  `build_tracer_provider`, not an exporter.
- **`services/monitoring/__init__.py`** — exports the new tracing surface.
- **`shared/config.py`** — three new `Settings` fields: `otel_exporter_otlp_endpoint`
  (`str | None`, unset = never dialled, **not** a `prod` boot requirement),
  `otel_traces_sample_ratio` (default 1.0, bounded 0–1), `otel_export_timeout_seconds`.

### Infrastructure / provisioning

- **`docker-compose.yml`** — added `otel-collector`
  (`otel/opentelemetry-collector-contrib:0.116.1`, OTLP 4317/4318) and `tempo`
  (`grafana/tempo:2.6.1`, HTTP 3200, `tempo_data` volume). The `api` service gained
  `OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4318` and a
  `depends_on: otel-collector (service_started)`. Grafana `depends_on: tempo`.
- **`infrastructure/docker/otel-collector/config.yml`** — traces-only pipeline:
  otlp receiver (grpc+http) → memory_limiter + batch → otlp/tempo.
- **`infrastructure/docker/tempo/tempo.yml`** — single-binary, local-filesystem
  Tempo (dev shape).
- **`infrastructure/docker/grafana/provisioning/datasources/tempo.yml`** — Tempo
  datasource (uid `tempo`), `tracesToMetrics` → Prometheus, `nodeGraph` disabled
  (no metrics-from-traces pipeline exists — deliberate, ADR 0016).
- **`.../datasources/prometheus.yml`** — added explicit `uid: prometheus` (the
  dashboard and `tracesToMetrics` reference it by uid).
- **`.../dashboards/api-overview.json`** (new) + `dashboards.yml` — dashboard
  "API — RED + traces": RED panels from Prometheus + three Tempo trace tables.
- **`infrastructure/docker/grafana/provisioning/alerting/api-rules.yml`** (new) —
  three symptom-level alerts (below).
- **`infrastructure/docker/prometheus/prometheus.yml`** — updated a stale comment:
  `rule_files` stays empty **by design** (Grafana-native alerting, not Prometheus
  + Alertmanager).

### Dependencies (`pyproject.toml`, `uv.lock`)

The whole `observability` extra was **promoted to base dependencies** (same
pattern Stages 2/3/4 used), because `@traced` sits on ~30 call sites and emits a
span on each — the SDK is no more optional than the logger. No extras remain.

### Docs

- **`docs/adr/0016-observability-stack.md`** (new) + ADR README index row.
- **`docs/architecture.md`** — re-framed to Stage 5; added tracing to the
  component map, a new "Tracing and the `@traced` seam" subsection with a
  provider-selection diagram, updated the `shared/` table, Infrastructure and
  Quality-gate sections, moved monitoring out of "Planned". Regenerated (below).
- **`docs/PROJECT_STATUS.md`** — Stage 5 marked complete (5/10, 50%), capabilities
  and next-milestone (Stage 6) updated.
- **`CLAUDE.md`** — current-state, layout, seams, tracing convention, deferred
  table updated.

---

## The `test_tracing.py` fix (resume point)

Two tests were failing on entry:
`test_a_raising_sync_call_records_the_exception_and_reraises` and its async twin.
Both assert `span.status.description == "<ExceptionType>"` (type name only).

**Root cause — not stale bytecode.** The fix (`record_exception=False,
set_status_on_exception=False` on `start_as_current_span`) had been applied to the
**async** wrapper only. The **sync** wrapper (`shared/observability.py:107`) still
used the default `set_status_on_exception=True`, so the SDK's own context-manager
exit **overwrote** the type-only status the decorator set in `_record_error` with
`f"{type}: {message}"` — i.e. `"ValueError: kaboom"` instead of `"ValueError"`.
The clean-cache rerun the previous checkpoint never finished would not have helped;
the sync path genuinely never had the fix.

**Fix.** Added `record_exception=False, set_status_on_exception=False` to the sync
wrapper's `start_as_current_span`, matching the async wrapper, so the decorator
owns exception recording on both paths.

Cleared every `__pycache__`, reran `tests/unit/test_tracing.py` → **27 passed**.
The 2 failures were the only ones; the other 25 were already green.

While bringing the file through the full gate, cleaned up pre-existing lint/type
debt in the test file itself (unused import, unused `noqa` directives, two
over-length lines, a `Tracer._real_tracer` attr-access typed via a local
`Any` alias, and a `Mapping | None` index guarded with an assert).

---

## Test counts (exact, from `uv run pytest`)

| | Command | Result |
|---|---|---|
| Tracing file | `uv run pytest tests/unit/test_tracing.py` | **27 passed** |
| Full suite (before) | Stage 4 baseline (docs) | 246 |
| Full suite (after) | `uv run pytest` | **273 passed, 9 skipped, 1 warning** |

Delta **+27**, all in `tests/unit/test_tracing.py`. The 9 skips are the pre-existing
opt-in layers (live Postgres/Redis, live Qdrant, live provider contract) — all
skip by default; **no collector or Tempo is required for the suite**. The 1
warning is the pre-existing Starlette `TestClient`/`httpx` deprecation.

Span-emission tests use OpenTelemetry's `InMemorySpanExporter` (no socket). The
hermetic guard has its own tests: `OTLPTracerProvider` raises under `test` even
with an endpoint set in config or in the OS environment
(`TestTestProfileCannotReachTheCollector`), and `build_tracer_provider` selects
local under `test`, OTLP under `dev`/`prod` with an endpoint
(`TestBuildTracerProviderSelection`).

---

## Lint / format / types (exact commands and results)

| Command | Result |
|---|---|
| `uv run ruff check .` | **All checks passed!** |
| `uv run ruff format --check .` | **66 files already formatted** |
| `uv run mypy .` | **Success: no issues found in 66 source files** |

---

## OTel packages pinned (exact versions)

Promoted to base dependencies:

- `opentelemetry-api==1.43.0`
- `opentelemetry-sdk==1.43.0`
- `opentelemetry-instrumentation-fastapi==0.64b0` (sibling release of 1.43.0)
- **`opentelemetry-exporter-otlp-proto-http==1.43.0`** — the OTLP exporter, added
  this stage (the extra had no exporter pinned before).

**http/protobuf, not gRPC:** not a weight argument (`grpcio` is already in the tree
via `qdrant-client`) — it keeps the collector's `:4318` receiver `curl`-able for
debugging and avoids a second package contending over `grpcio`'s version range. The
collector accepts both protocols. ADR 0016.

---

## Profile-keyed tracer-provider seam (mechanism, not "mocked in tests")

Same construction as the Anthropic (ADR 0009) and Voyage (ADR 0011) clients, third
vendor. `OTLPTracerProvider.__init__` raises `RuntimeError` when
`settings.is_test`, **before** the endpoint is read — so no import order, fixture,
or monkeypatch can turn a unit test into a network call. `build_tracer_provider`
keys on the **profile**, not on collector reachability: a dev with a collector
running and an endpoint exported still gets the local provider under `test`.

Confirmed at container level this stage (see boot section): `test` →
`provider: "local", reason: "test profile"`; `prod` with no endpoint →
`provider: "local", reason: "OTEL_EXPORTER_OTLP_ENDPOINT not set"`; `dev` compose
with the endpoint → `provider: "otlp"`.

---

## Alert thresholds and justification

Symptom-level only (a caller would notice each); provisioned via Grafana-native
unified alerting — no Alertmanager container.

| Alert | Threshold | `for` | Severity | Why |
|---|---|:--:|---|---|
| **API error rate** | 5xx / total **> 5%** | 5m | critical | Past 5%, at any real volume, a slice of traffic is broken (a dependency, the model call, or the code) — not one unlucky client. `clamp_min` on the denominator stops an idle service evaluating 0/0. The 5m `for` rides out a deploy blip or datastore reconnect. |
| **Chat p99 latency** | **> 30s** on `/v1/chat/completions` | 10m | warning | Route-scoped so `/health` (10s healthcheck) and `/metrics` (15s scrape) don't dilute it. A grounded answer runs up to `AGENT_MAX_STEPS`(6) model calls each bounded by a 60s timeout, so seconds are legitimate; 30s means the worst request is burning half a single call's budget — outside normal even multi-step. Warning: slow is degradation, not outage. |
| **`/ready` not_ready** | 503 count **> 0** | 2m | critical | Any occurrence — there is no healthy background level of `not_ready`. The 2m `for` rides out a restart/reconnect, which matters because `startup()` never raises (ADR 0005): an unreachable store yields an un-ready pod, and this alert makes that visible. |

These are starting points from what this stack does, not tuned against production
traffic — Stage 9 owns SLOs and the load levels that would move them.

---

## Span attribute hygiene (per scope point 8)

Every span `@traced` emits carries **exactly two attributes**, both read off the
function object at decoration time — never from a call:

- `code.function` — the function's qualified name (also the span name)
- `code.namespace` — its module

There is structurally no code path from a wrapped call's arguments or return value
onto a span. On error the exception is recorded as an **event** (which carries the
message + traceback, as the DEBUG log already did) and the status **description is
the exception type name only** — the runtime message is never repeated as a
searchable status. `TestSpanHygiene` asserts that a secret/query/excerpt passed as
an argument or returned never appears anywhere on the span (name, attributes,
events, or status), on both the sync and async paths. **Confirmed in Tempo** (boot
section): the trace fetched by id showed attribute keys `['code.function',
'code.namespace']` and nothing else.

---

## architecture.md updated + regenerated

`docs/architecture.md` was edited (source of truth) and the HTML regenerated with:

```
uv run python scripts/build_architecture.py
```

Output: rendered two changed diagrams (`flowchart-td-*` component map, new
`flowchart-lr-*` provider-selection diagram), removed one orphaned diagram, wrote
`architecture.html`. Verified drift-free:

```
uv run python scripts/build_architecture.py --check   # architecture.html is up to date.
uv run pytest tests/unit/test_architecture.py         # 14 passed
```

---

## Full compose boot + real trace in Tempo (exact commands and output)

```
docker compose up -d --build
```

All 8 containers came up; `docker compose ps` showed `api` **healthy**, plus
`postgres`/`redis` healthy and `otel-collector`, `tempo`, `prometheus`, `grafana`,
`qdrant` up.

**API installed the exporting provider** (compose runs `ENVIRONMENT=dev` with the
endpoint set):

```
"tracing.provider_selected"  provider=otlp  endpoint=http://otel-collector:4318  sample_ratio=1.0
"tracing.configured"         provider=OTLPTracerProvider  instrumented=fastapi
```

Collector: `Everything is ready. Begin running and processing data.` (no errors).

Generated traffic (`curl` of `/health`, `/ready`, `/version`), then queried Tempo:

```
curl --get http://localhost:3200/api/search \
  --data-urlencode 'q={ resource.service.name = "api" }' --data-urlencode 'limit=20'
```

→ **20 traces**, `rootServiceName: "api"`, including request traces
(`RequestContextMiddleware.dispatch`, 2–6 spans) and **startup** traces
(`_build_engine` 7 spans, `DatastoreRegistry.startup` 4, `apply_pending` 2).

Fetched one trace whole:

```
curl http://localhost:3200/api/traces/b9b5f986d871fef7d33dfc6be861ba1c
```

→ 2 spans, `RequestContextMiddleware.dispatch` (root) → `health` (child) —
confirming real parent/child nesting — with span-attribute keys **exactly**
`['code.function', 'code.namespace']`, confirming hygiene end-to-end.

## Both container profiles booted standalone (container-boot rule)

Ran the built image (`production-llm-platform/api:0.1.0`) directly:

- **test** — `docker run -e ENVIRONMENT=test -e LOG_LEVEL=INFO -p 8010:8000 ...`:
  `/health` → `200 {"environment":"test"}`; log
  `tracing.provider_selected provider=local reason="test profile"`,
  `configured provider=LocalTracerProvider`. The hermetic guard holds at container
  level — `test` cannot construct the exporting provider.
- **prod** — `docker run -e ENVIRONMENT=prod` + dummy datastore URLs and dummy
  `ANTHROPIC_API_KEY`/`VOYAGE_API_KEY` (the prod validator requires them), **no**
  `OTEL_EXPORTER_OTLP_ENDPOINT`: container reached **healthy**; `/health` → `200
  {"environment":"prod"}`; `/ready` → `503 not_ready` (dummy datastores
  `unavailable` — correct fail-loud, ADR 0005). Log
  `tracing.provider_selected provider=local reason="OTEL_EXPORTER_OTLP_ENDPOINT not
  set"` (WARNING) — **confirming tracing is not a prod boot requirement**, and the
  service boots and serves untraced when no collector is configured.

Both standalone boot-check containers were removed afterward; the compose stack was
left running.

---

## Deviations from scope

- **The Stage 1 `SpanExporter` ABC was retired, not implemented.** Scope point (an
  implicit "concrete exporters arrive in Stage 5") is satisfied by adopting OTel's
  own `SpanExporter` contract rather than building a homegrown one that loses
  information. Full rationale in `services/monitoring/base.py` and ADR 0016. This is
  the same call as Stage 3 dropping `langchain` and Stage 4 preferring
  `llama-index-core` primitives.
- No other deviations. Metrics staying on Prometheus (traces-only collector) is a
  scope item, not a deviation — stated explicitly in ADR 0016.

---

## Known limitations / deliberately deferred

- **OTel metrics export is out of scope this stage (deliberate, ADR 0016).** The
  collector carries **traces only**; metrics stay on `prometheus-client` `/metrics`
  scraped by Prometheus. Consequently Grafana's **service map / node graph are
  switched off** (they need the collector's spanmetrics/servicegraph connectors —
  the metrics pipeline this ADR declined) rather than left rendering "No data".
- **The `/ready` alert is armed but quiet locally.** It counts what `/ready` *told
  a caller*, and nothing in the compose stack polls `/ready` (the Docker
  healthcheck hits `/health`, which by design never probes a dependency). In Stage
  7 the kubelet readiness probe polls it and the rule works as written. Documented
  in the rule's own annotation.
- **Local-development Tempo shape only:** single-binary, local-filesystem backend,
  no TLS on the collector→Tempo hop, sampling ratio 1.0. A real deployment splits
  Tempo's components, uses object storage, and tunes sampling — Stage 7 (K8s) /
  Stage 9 (reliability).
- **Tempo trace-by-id GET needs a flush window** for very recent traces (the search
  API reads the ingester; the single-trace GET reads flushed blocks). Not a defect —
  the pipeline is proven by the search returning 20 traces and one fetched-whole
  trace above.
- Prior-stage deferrals stand unchanged (auth, evaluation, K8s, reliability).
