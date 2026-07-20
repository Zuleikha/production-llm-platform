# Stage 5 — Observability

## Objective

Give the `@traced` seam (Stage 1, `shared/observability.py`) a real OpenTelemetry
backend — traces exported to a collector, dashboards and alerting provisioned
as code — without changing any of the ~30 existing call sites that already use
`@traced`.

## Decided before this stage (do not re-litigate)

- **Trace backend: Grafana Tempo**, receiving OTLP from a new OTel Collector
  container. Reason: Grafana + Prometheus are already provisioned as code in
  this repo; Tempo is a native Grafana datasource (same provisioning pattern
  as `infrastructure/docker/grafana/provisioning/datasources/prometheus.yml`),
  so this adds one container family instead of two unrelated ones (collector
  + Jaeger + Alertmanager).
- **Alerting: Grafana's built-in unified alerting**, provisioned as code
  (YAML under `infrastructure/docker/grafana/provisioning/alerting/`). No
  separate Alertmanager container — Grafana 11 (already pinned) ships this
  natively.
- **Metrics stay on the existing `prometheus-client` `/metrics` path.** Do
  **not** stand up a second, competing metrics pipeline through the OTel
  Collector. The collector in this stage handles **traces only**. State this
  explicitly as a decision in the ADR below — it's a deliberate scope cut, not
  an oversight.

Write one ADR (0016) capturing all three of the above as a single "observability
stack" decision — Context / Decision / Consequences, same format as 0001-0015.

## Scope

1. **Dependencies.** The `observability` extra (`pyproject.toml`) currently
   pins `opentelemetry-api`, `opentelemetry-sdk`,
   `opentelemetry-instrumentation-fastapi` — it has **no OTLP exporter
   package pinned yet**. Add the exporter (exact version, state it in the
   self-report) and **promote the whole `observability` extra to base
   dependencies**, following this repo's established pattern (`retrieval` was
   promoted in Stage 4 when it went from declared-but-unused to actually
   wired in). State exact versions pinned.

2. **`docker-compose.yml`**: add an `otel-collector` service (OTLP receiver on
   4317/4318, exporting to Tempo) and a `tempo` service, both on the existing
   `platform` network. Add a Tempo datasource under
   `infrastructure/docker/grafana/provisioning/datasources/`, same shape as
   the existing `prometheus.yml` one.

3. **Extend `shared/observability.py`'s `@traced` decorator to also emit a
   real OTel span** (start/end, function qualname as span name, exception
   recorded on error) alongside its existing structured DEBUG log — the debug
   log stays, this is additive, not a replacement. No call site changes.
   Async and sync both still supported.

4. **Hermetic-by-construction, same pattern as ADR 0009/0011**: the `test`
   profile must not be able to dial the collector. Wire tracer-provider setup
   through a seam (`build_tracer_provider` or equivalent) that gives `test` a
   no-op/in-memory provider and gives `dev`/`prod` the real OTLP exporter,
   keyed on profile, not on whether a collector happens to be reachable.
   State the mechanism explicitly in the self-report.

5. **FastAPI auto-instrumentation** (`opentelemetry-instrumentation-fastapi`)
   wired in `app.py` lifespan, behind the same profile seam as point 4 — it
   must not attempt any network call in `test`.

6. **Grafana dashboards provisioned as code** (JSON + a `dashboards.yml`
   provisioning entry, same mechanism as the existing dashboards
   provisioning stub): at minimum, request rate/error-rate/latency (RED
   metrics, from the existing Prometheus datasource) and a trace-search view
   (Tempo datasource).

7. **Alerting rules provisioned as code** (Grafana unified alerting YAML), on
   symptom-level signals — not internals. At minimum: error-rate threshold,
   p99 latency threshold, and `/ready` reporting `not_ready`. State the exact
   thresholds chosen and why (even a rough justification) in the self-report.

8. **Span/log hygiene.** The existing logging rule ("never log
   PII/secrets/tokens, nor queries/excerpts — attacker-influenced",
   `CLAUDE.md`) applies identically to span attributes. Do not attach
   request/response bodies, retrieved chunk text, or raw user input as span
   attributes. State what attributes each new span carries.

## Required conventions (do not deviate)

- One ADR (0016, continuing sequentially) — the combined observability-stack
  decision from the section above.
- `docs/PROJECT_STATUS.md` updated at end of stage — mark stage 5 complete,
  leave other stages' rows untouched.
- `CLAUDE.md` stays under 150 lines. State before/after line count.
- `docs/architecture.md` updated to reflect the collector, Tempo, and the
  trace flow; regenerate via `uv run python scripts/build_architecture.py`
  and state the exact command run. Never hand-edit `architecture.html`.
- New diagrams (if any): pre-rendered static SVG, no CDN dependency (ADR
  0010). Don't use a Mermaid keyword (`graph`, `end`, etc.) as a node id.
- `pyproject.toml` keeps `[tool.uv] link-mode = "copy"`.
- No hardcoded secrets anywhere.
- Stub folders for other stages (`evaluation`, `security`) are untouched
  this stage.
- **Container boot is required before self-report, not deferrable.** Bring
  up the **full compose stack** (`docker compose up -d --build`), generate a
  few chat requests, and confirm a trace actually lands in Tempo (curl
  Tempo's search API or equivalent) — not just that containers report
  "healthy." State the exact commands and output.

## Testing requirements

- All new code has unit test coverage. State exact test count before/after
  using what `uv run pytest` actually reports.
- Span-emission tests use OpenTelemetry's **in-memory span exporter**
  (`InMemorySpanExporter`), not a live collector — full suite must still pass
  with zero network calls and no collector/Tempo running. This mirrors the
  existing hermetic-provider pattern (ADR 0009/0011), applied to tracing.
- `ruff check .`, `ruff format --check .`, `mypy` (strict) all clean. State
  exact commands and actual exit status.
- One integration-style test confirming: in `dev`/`prod`-shaped config, the
  tracer provider seam selects the real exporter; in `test`, it selects the
  no-op one. Assert on which one was selected, not on network behavior.

## Explicit constraints

- Do not commit or push. Build, test, self-report only — commit happens only
  after independent manual verification, as a separate step.
- Do not touch stub folders for other stages.
- Use the canonical stage name `stage-05-observability` verbatim everywhere.
- No paid external APIs in this stage (Tempo/Collector/Grafana are
  self-hosted) — the "confirm before any live call" rule does not apply here,
  there is nothing to confirm.

## Self-report requirements

Write `docs/stage-summaries/stage-05-observability.md` containing:

- What was implemented, file by file
- Exact test count before/after and the command used
- Exact ruff/mypy/format commands run and their actual results
- CLAUDE.md line count before/after
- Exact OTel exporter package + version pinned
- Confirmation of the profile-keyed tracer-provider seam mechanism (not just
  "mocked in tests")
- The alert thresholds chosen and rough justification
- What attributes each new span carries (hygiene confirmation per point 8)
- Confirmation `docs/architecture.md` was updated and
  `scripts/build_architecture.py` was re-run, with the exact command
- Confirmation the full compose stack was brought up and a real trace was
  observed in Tempo, with the exact commands and output
- Any deviations from this scope and why
- Known limitations or deliberately deferred items (e.g. OTel metrics export
  is explicitly out of scope this stage — see decided-before-stage section)

State only what was directly run and observed this stage. Do not restate
prior-stage claims as if re-verified.
