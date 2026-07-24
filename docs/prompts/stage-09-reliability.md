# Stage 9 — Reliability

## Objective

Make the platform survive load and partial failure on purpose, not by luck.
Concretely: a load-testing harness against the real stack, chaos/failure
testing of every datastore and the Anthropic call, formal SLOs backed by
Grafana alerts, four resilience patterns (pool tuning, a circuit breaker
around the Anthropic call, prompt caching, context compaction), and the OTel
metrics-export pipeline deferred from Stage 5/6 (ADR 0016). Everything here
is either already named as Stage 9's in `docs/PROJECT_STATUS.md` / `CLAUDE.md`'s
deferred table, or is a direct, previously-flagged consequence of a Stage 3–8
decision (ADR 0008's untuned TTL, the fail-open rate limiter, the absent
provider-call retry/circuit-break). Nothing new is invented this stage.

## Decided before this stage (do not re-litigate)

- **Load testing tool: Locust, not k6.** Pure Python, matches the project's
  Python-first tooling (`uv`), and scripts as ordinary Python rather than a
  second language's test DSL. Lives at `tests/load/locustfile.py` — outside
  `tests/unit/` because it is not part of the hermetic pytest suite and must
  never be collected by `pytest`.
- **Two load-test modes, not one — this is the load-testing analogue of ADR
  0015's opt-in live contract test, and for the same reason: a real Anthropic
  call costs money and real latency per request.**
  1. **Primary, cost-free mode: against the `test`-profile container.**
     `ScriptedLLMClient`'s echo behavior plus real Postgres/Redis/Qdrant
     containers. This is what drives pool tuning, the rate-limiter chaos
     test, and the circuit-breaker mechanics — none of that needs a real
     model response, only real datastore contention and real HTTP concurrency.
  2. **Secondary, opt-in, manual, billable mode: against a real `dev`-profile
     container with a real `ANTHROPIC_API_KEY`.** A short, small run (state
     the exact user count/duration used) to get real end-to-end latency
     numbers for the SLOs below and to prove the circuit breaker trips against
     the real client, not just the fault-injecting test double. Never run in
     CI. Document the exact command and a redacted summary of what it cost in
     the self-report — do not paste a real key or its output verbatim if it
     contains one.
  Neither mode is a CI gate. Load and chaos testing need a running stack (the
  same reason live-datastore/live-Qdrant/live-provider tests already skip by
  default) — this is consistent with the project's opt-in-layer precedent, not
  a new exception to it.
- **Chaos testing is a manual runbook, not an automated CI job.** For each of
  Postgres, Redis, and Qdrant: start a Locust run against the `test`-profile
  compose stack, `docker compose stop <service>` mid-run, observe, `docker
  compose start <service>`, confirm recovery. Expected behavior per service is
  already specified by earlier ADRs — this stage's job is to **prove it under
  real concurrent load**, not to invent new behavior:
  - **Postgres down:** `/ready` reports `not_ready` (503) per the existing
    readiness probe; in-flight chat requests fail loudly (ADR 0007's known gap
    — `/ready` does not check schema version, but a fully-down Postgres is
    caught). Confirm no crash loop (`startup()` never raises, ADR 0005).
  - **Redis down:** conversation history reads degrade to a Postgres read
    (ADR 0008's one sanctioned fail-loud exception) and the rate limiter
    fails open + logs (ADR 0019) — under **real concurrent load**, not the
    single-request manual curl Stage 8 used. This is the "Stage 8's
    rate-limiter fail-open... is a reliability surface Stage 9 owns" item
    from `docs/PROJECT_STATUS.md`: **prove and instrument it, do not reverse
    it.** The fail-open decision itself is not up for debate this stage.
  - **Qdrant down:** confirm the agent still answers without
    `document_search` (Stage 4's `with_tools`-only-when-Qdrant-is-up wiring)
    rather than the whole chat endpoint failing.
  - **Anthropic unreachable/erroring:** covered by the circuit breaker below,
    using a fault-injecting test double — the real API is never deliberately
    taken down, obviously.
- **Per-conversation concurrency control (ADR 0008's other known gap) is
  explicitly OUT of scope this stage.** It is a known issue in `CLAUDE.md` but
  is **not** in the Stage 9 deferred-table row (`docs/PROJECT_STATUS.md` /
  `CLAUDE.md` list: load/chaos testing, SLOs, OTel metrics export, pool
  tuning, circuit breaking, prompt caching, context compaction — concurrency
  control is not among them). Do not build row-locking or optimistic
  versioning this stage; restate it as still-deferred in the self-report.
- **Circuit breaker: wraps `LLMClient`, does not touch the Anthropic SDK's
  own retry behavior.** The SDK already retries certain transient errors
  (`CLAUDE.md`: "retry / circuit breaking around the Anthropic call beyond
  the SDK's defaults" is what's missing, not retries themselves). This stage
  adds a breaker *around* `AnthropicClient.stream` — implementing the same
  `LLMClient` protocol so `AgentGraph` takes it as a drop-in — that trips
  after N consecutive failures and fails fast (no more calls, no more
  waiting for the SDK's own retry/backoff) until a cooldown elapses, then
  allows one trial call (half-open) before fully closing again.
  - Trips on transport/server-side failure only —
    `anthropic.APIConnectionError`, `anthropic.APITimeoutError`,
    `anthropic.InternalServerError` (and the equivalent for whatever the
    installed SDK version actually raises — verify against the pinned
    `anthropic` package, do not guess the exception hierarchy). **Does not
    trip on a 400** (e.g. the existing "no sampling params" rejection,
    ADR 0006) — that is a caller bug, not the provider being down, and
    opening the breaker over it would take down the whole endpoint for a
    request-shape mistake.
  - Configurable via `Settings`: failure-count threshold and cooldown
    duration (name them, pick sane defaults, state and justify them).
  - When open: fail fast with the uniform error envelope, a new error `type`
    distinct from the existing `401`/`429` shapes (e.g. `503
    provider_unavailable`) — do not let an open breaker surface as a bare
    500 or hang on the SDK's own timeout.
  - Testable hermetically: add a small fault-injecting `LLMClient` test
    double (alongside the existing fakes, e.g. in `tests/fakes.py`) that
    raises a given exception N times then succeeds. The real
    `AnthropicClient` is never exercised by this test — same hermetic
    posture as everything else touching the Anthropic seam (ADR 0009).
- **Prompt caching: cache_control breakpoints on the system prompt and the
  tool specifications, contained entirely inside `AnthropicClient`.** The
  `LLMClient` protocol's wire shape (`system: str`, `tools: Sequence[dict]`)
  does not change — `AnthropicClient.stream` converts the plain system
  string into Anthropic's content-block form with a `cache_control` marker
  and adds one to the last tool spec, internally, before calling the SDK.
  Both are static within a run and often across turns of the same
  conversation, which is exactly what prompt caching is for. **Verify the
  exact `cache_control` field shape and any beta-header requirement against
  the currently-pinned `anthropic` SDK version at build time** — do not
  assume it matches an older or newer SDK's mechanics. A hermetic unit test
  can only assert the request shape includes the cache_control marker
  (`ScriptedLLMClient`/a fake transport, not a real call); actual cache-hit
  behavior (`cache_creation_input_tokens`/`cache_read_input_tokens` in the
  response) can only be confirmed against the real API — fold that check
  into this stage's opt-in live-contract-test run (ADR 0015's existing
  double-gated test), not a new CI-facing assertion.
- **Context compaction: deterministic message-count windowing, not
  LLM-summarization.** When a conversation's message list passed to the model
  exceeds a configurable window, drop the oldest turns before calling
  `self._llm.stream(...)` in `AgentGraph._reason` — **the full history stays
  in `AgentState["messages"]` and stays exactly what gets persisted to
  Postgres** (`services/orchestrator/conversations.py`); only the outbound
  call to the model is windowed. A summarization call is explicitly rejected
  this stage: it is a second model call with its own cost, latency, and
  failure mode, and a windowed-context conversation is honestly presented to
  the user (older context is gone, not silently and lossily compressed) —
  state this trade-off plainly in the ADR and note summarization as possible
  future work if the message-count proxy proves wrong once real load-test
  data exists. Configurable via `Settings` (name it, pick a sane default,
  state why).
- **Pool tuning is data-driven this stage, not guessed twice.** Run the
  primary (cost-free) Locust mode against realistic concurrency, observe
  where `db_pool_max_size` / `redis_pool_max_connections` /
  `qdrant_pool_max_connections` (`shared/config.py`) actually queue or time
  out, and set new defaults from what was observed — cite the concurrency
  level and the observed behavior in the self-report, not a re-guessed
  number. Also revisit `conversation_cache_ttl_seconds` (ADR 0008 flagged
  its 300s default as an untuned guess) against the same load data: either
  change it with a stated reason, or explicitly keep it and say why the load
  test didn't surface a reason to change it. Either outcome is acceptable;
  an unexamined default is not.
- **OTel metrics pipeline: spanmetrics + servicegraph connectors added to the
  existing collector, additive to Prometheus, not a replacement.** ADR 0016
  deliberately kept the collector traces-only because a second pipeline for
  *the same numbers* (the app's own instrumented RED metrics) would create
  two disagreeing sources of truth. Spanmetrics-derived metrics are a
  **different signal** — RED metrics computed from trace spans, purely to
  drive Tempo's `serviceMap`/`nodeGraph` views, which
  `infrastructure/docker/grafana/provisioning/datasources/tempo.yml`
  currently ships disabled for exactly this reason. This is not re-opening
  ADR 0016's decision; it is building the thing ADR 0016 named as future
  work. Concretely: add `spanmetrics`/`servicegraph` connectors and a
  `prometheus` exporter to
  `infrastructure/docker/otel-collector/config.yml`'s pipeline, add the
  collector's new metrics endpoint as an additional scrape target in
  `infrastructure/docker/prometheus/prometheus.yml` (additional target, the
  app's own `/metrics` scrape is untouched), and flip `nodeGraph`/`serviceMap`
  to enabled in the Tempo datasource now that real series will back them.
  Still hermetic by construction — this only touches `dev`/`prod` container
  config, no new test-facing seam; `test` still gets `LocalTracerProvider`
  and never reaches the collector (ADR 0016 unchanged).
- **SLOs are targets to alert against, not historical numbers.** This project
  has no real production traffic, so "SLO" here means a stated target
  (availability, latency) backed by a Grafana alert rule, in the same
  symptom-level style as Stage 5's three existing rules — not a measured
  historical achievement. Write them in a new `docs/slo.md` (or a new section
  in `docs/architecture.md` — state which and why) and add the concrete new
  alert rules this stage's new mechanisms need:
  1. A rule for the circuit breaker opening (a caller-visible new failure
     mode Stage 5's rules don't cover).
  2. A rule for the rate limiter's Redis fail-open path firing (instruments
     the surface named above — an event worth knowing about even though the
     decision is to keep failing open).
  Keep Stage 5's three existing rules unchanged; add to them, don't replace.

Write one ADR (0020, continuing sequentially) covering: the load-testing tool
and its two-mode split, the chaos-testing runbook and expected behavior per
datastore, the circuit breaker's trip conditions and config, the prompt
caching mechanism and its hermetic-vs-live test split, the context-compaction
windowing decision (and the explicit summarization rejection), the pool
tuning results, the metrics-pipeline addition (and why it doesn't reopen ADR
0016), and the SLOs plus their two new alert rules.

## Scope

1. **`tests/load/locustfile.py`** — Locust scenarios against
   `/v1/chat/completions` (needs a valid API key — mint one with
   `scripts/generate_api_key.py`) and the unauthenticated `/health`/`/ready`.
   Document the exact commands for both modes (primary `test`-profile run,
   secondary opt-in `dev`-profile run) in the new ADR and/or a short
   `tests/load/README.md` — not in `docs/architecture-history`, which is a
   separate, gitignored artifact unrelated to this.

2. **Chaos-testing runbook** — a short, concrete document (new
   `docs/runbooks/chaos-testing.md`, or state a different location and why)
   listing the exact `docker compose stop`/`start` sequence per datastore,
   what to watch (which endpoint, which log line, which Grafana panel), and
   the expected outcome per the decisions above. This stage's actual chaos
   *runs* happen manually against the real machine — capture what was
   observed in the self-report, not just what the runbook says should
   happen.

3. **Circuit breaker** — a generic primitive (state where it lives —
   `shared/resilience.py` is a reasonable default given it's cross-cutting
   infra, not LLM-specific, but your call) wrapping `LLMClient` (concrete
   class alongside `AnthropicClient`/`ScriptedLLMClient` in
   `services/orchestrator/llm.py`, or a sibling module — state which). New
   `Settings` fields for the failure threshold and cooldown. Wired wherever
   `build_llm_client` is consumed so the breaker sits between `AgentGraph`
   and the real client. New fault-injecting test double for hermetic
   coverage.

4. **Prompt caching** — inside `AnthropicClient.stream` only, per the
   decision above. No protocol or call-site changes outside that class.

5. **Context compaction** — windowing logic in `AgentGraph._reason` (or a
   small helper it calls), touching only what's sent to `self._llm.stream`,
   never `AgentState["messages"]` itself or what
   `services/orchestrator/conversations.py` persists. New `Settings` field
   for the window size.

6. **Pool tuning** — updated defaults in `shared/config.py` for
   `db_pool_max_size` / `redis_pool_max_connections` /
   `qdrant_pool_max_connections` / `conversation_cache_ttl_seconds`, each
   justified by an observed number from the primary Locust mode, in the ADR
   and self-report.

7. **OTel metrics pipeline** — `infrastructure/docker/otel-collector/config.yml`
   (add spanmetrics/servicegraph connectors + prometheus exporter),
   `infrastructure/docker/prometheus/prometheus.yml` (new scrape target),
   `infrastructure/docker/grafana/provisioning/datasources/tempo.yml`
   (enable `nodeGraph`/`serviceMap`). No application code changes — this is
   infra-config only.

8. **SLOs + two new Grafana alert rules** — `docs/slo.md` (or equivalent,
   state which) plus new rule files under
   `infrastructure/docker/grafana/provisioning/alerting/`, matching Stage 5's
   existing style (threshold, `for`, severity, one-line why) for: circuit
   breaker open, rate-limiter Redis fail-open.

9. **Tests** — hermetic, CI-facing:
   - Circuit breaker: closed → trips after N failures → fails fast while
     open → half-open trial → recovers, using the new fault-injecting
     double. No real Anthropic call.
   - Context compaction: a conversation longer than the window sends only
     the windowed slice to the (fake) `LLMClient`, while the full history is
     still what's returned/persisted.
   - Prompt caching: the constructed request (via a fake transport/spy on
     `AnthropicClient`, not a real call) includes the `cache_control` marker
     on the system prompt and the last tool spec.
   - Any new pure-logic pieces (pool-size validation if you add any,
     settings defaults) get ordinary unit coverage.
   Explicitly **not** hermetic, and explicitly **not** required-CI**:
   Locust runs and the chaos runbook — both are manual, both are documented
   above as such.

10. **Docs** — ADR 0020; `docs/architecture.md` new "Reliability" section
    (circuit breaker, prompt caching, context compaction, the metrics
    pipeline addition), regenerated; `docs/slo.md` (or wherever decided);
    `tests/load/README.md` (or wherever decided); `CLAUDE.md` current-state +
    deferred-table update (Stage 9's row is done — remove everything in it
    that this stage actually built; anything *not* built this stage, e.g.
    per-conversation concurrency control, stays a known issue, restated
    accurately); `docs/PROJECT_STATUS.md` stage-9 marked complete.

## Required conventions (do not deviate)

- One ADR (0020, continuing sequentially) — the combined decision from
  above.
- `docs/PROJECT_STATUS.md` updated at end of stage — mark stage 9 complete,
  leave other stages' rows untouched.
- `CLAUDE.md` stays under 150 lines. State before/after line count.
- `docs/architecture.md` updated (new Reliability section); regenerate via
  `uv run python scripts/build_architecture.py` and state the exact command.
  Never hand-edit `architecture.html`.
- **Standing close-out step, gitignored, do not skip:** per
  `docs/cc-stage-prompt-template.txt` / `docs/architecture-history/README.md`,
  after this stage's completion commit lands, add stage 9's row to `_STAGES`
  in `docs/architecture-history/generate.py` and re-run it. This folder is
  never committed or staged.
- `@traced` on new application functions (the breaker's call wrapper, the
  compaction helper), same exemptions as always
  (`shared/{logging,observability}.py`, async generators/lifespan, LangGraph
  nodes — note `_reason`/`_act` are already exempt as LangGraph nodes, so the
  compaction helper should be a plain function/method they call, not itself
  a node, if it needs `@traced`).
- Never log a raw API key, message content, or retrieved excerpt — same
  standing rule, applies to any new logging (breaker state transitions,
  compaction events, fail-open events) exactly as it applies everywhere else.
- mypy strict, ruff clean, tests mirror source, write the failing test
  first.
- **Container boot is required, not deferrable** (standing CLAUDE.md rule).
  Build and run in both `prod` and `test` profiles; additionally boot the
  full `docker compose` stack for the chaos runbook and the primary Locust
  mode, since both need the real Postgres/Redis/Qdrant containers.
- Do not weaken any existing hermetic seam (ADR 0009/0011/0016) while adding
  the circuit breaker or prompt caching — `test` still cannot construct a
  real `AnthropicClient`, full stop.

## Testing requirements

- New unit tests for the circuit breaker, context compaction, and prompt
  caching's request shape, all passing; state the new total test count
  against the Stage 8 baseline of 362.
- `ruff check .`, `ruff format --check .`, `mypy` (strict) all clean.
- The real container-boot check from the section above, with actual output
  captured.
- Primary (cost-free) Locust run: state user count, spawn rate, duration,
  and the pool-tuning numbers observed.
- Chaos runbook executed for Postgres, Redis, and Qdrant against the running
  stack under the primary Locust load — actual observed behavior (status
  codes, log lines, which Grafana panel moved) captured, not paraphrased.
- Secondary (opt-in, billable) Locust run against a real `dev`-profile
  container: state exact scale used and the real latency numbers obtained,
  redacting any key material.
- Circuit breaker proven against the real `AnthropicClient` at least once in
  the secondary run if practical (e.g. a deliberately wrong endpoint or a
  killed network path) — if not practical, say so and rely on the hermetic
  fault-injection tests, stated explicitly as the fallback.
- Existing test suite (everything from prior stages) still passes
  unmodified.

## Explicit constraints

- Do not commit or push. Build, test, self-report only — commit happens
  only after independent manual verification, as a separate step.
- Do not add a required CI job for load testing or chaos testing — both stay
  manual/opt-in, matching the eval Tier 2 / live-contract-test precedent.
- Do not implement per-conversation concurrency control (row locking,
  optimistic versioning) — explicitly out of scope this stage, per the
  decision above.
- Do not implement LLM-based context summarization — deterministic windowing
  only, per the decision above.
- Do not reverse the rate limiter's fail-open behavior (ADR 0019) — this
  stage proves and instruments it under load, it does not change the
  decision.
- Do not add a second, competing metrics source for the app's own RED
  metrics — the new pipeline is spanmetrics-derived, additive, and exists
  only to drive the service map/node graph.
- Do not touch the Anthropic SDK's own retry configuration as a substitute
  for the circuit breaker — they are different mechanisms and both should
  exist.
- Use the canonical stage name `stage-09-reliability` verbatim everywhere.

## Self-report requirements

Write `docs/stage-summaries/stage-09-reliability.md` containing:

- What was implemented, file by file
- `CLAUDE.md` line count before/after, and confirmation the Stage 9
  deferred-table row was updated to reflect exactly what was and wasn't
  built (concurrency control restated as still deferred)
- The circuit breaker's exact trip conditions, threshold/cooldown values and
  why, and confirmation of the hermetic fault-injection test results
- The prompt-caching mechanism's exact shape against the pinned `anthropic`
  SDK version, and whether the opt-in live run actually observed a cache
  hit (`cache_read_input_tokens` > 0 on a repeat call) or not
- The context-compaction window size chosen and why, with confirmation the
  full history is still what's persisted to Postgres
- The pool-tuning numbers: old defaults, new defaults, the concurrency level
  at which the old defaults showed queuing/timeouts, for each of the three
  pools plus the conversation cache TTL
- The exact chaos-runbook commands run and what was actually observed for
  Postgres/Redis/Qdrant down, including actual status codes and log lines
- The exact Locust commands for both modes, with real numbers from each
  (throughput/latency for the primary mode, real end-to-end latency for the
  secondary mode)
- The two new Grafana alert rules added, in the same table style as Stage
  5's three, plus confirmation Stage 5's originals are unchanged
- Confirmation of the full Python quality gate (test count vs the 362
  baseline, ruff/mypy/format results)
- Confirmation `docs/architecture.md` was updated and
  `scripts/build_architecture.py` was re-run, with the exact command
- Confirmation the `docs/architecture-history` close-out step was run (or an
  explicit note that it wasn't yet, if timing means it runs post-commit per
  the documented self-healing behavior)
- Any deviations from this scope and why
- Known limitations or deliberately deferred items (per-conversation
  concurrency control, LLM-based summarization, any pool numbers that are
  still a best guess because the load test didn't stress that path) — restate
  why, even though each is already agreed above

State only what was directly run and observed this stage. Do not restate
prior-stage claims as if re-verified.
