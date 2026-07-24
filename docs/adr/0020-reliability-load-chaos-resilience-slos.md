# ADR 0020 — Reliability: load & chaos testing, circuit breaker, prompt caching, context compaction, pool tuning, the OTel metrics pipeline, and SLOs

- **Status:** Accepted
- **Date:** 2026-07-24
- **Stage:** 9 (Reliability)
- **Builds on:** ADR 0005 (`/health` vs `/ready`, `startup()` never raises, pool
  settings), ADR 0006 (agent loop; the "no sampling params" 400), ADR 0008 (Redis
  degrades rather than fails loud; the untuned 300s cache TTL), ADR 0009 (the
  Anthropic client is hermetic by construction), ADR 0015 (opt-in, double-gated
  live contract test), ADR 0016 (traces-only collector; Grafana-native alerting;
  service-map/node-graph shipped disabled), ADR 0019 (rate limiter fails open).

## Context

Through Stage 8 the platform survives partial failure largely *by luck*: nothing
loads it on purpose, nothing proves the datastore failure modes under concurrency,
there is no retry/circuit-breaking around the paid Anthropic call beyond the SDK's
own, a long conversation will eventually exceed the context window and fail, the
system prompt and tool specs are re-sent uncached every turn, the pool sizes and
the conversation-cache TTL are unmeasured guesses, and the OTel collector carries
traces only (so Grafana's service-map/node-graph views are switched off). Every
one of those is either named in the Stage 9 deferred-table row or a directly
flagged consequence of a Stage 3–8 decision. **Nothing new is invented this
stage** — this ADR records how each is closed.

## Decisions

### 1. Load testing — Locust, two modes, never a CI gate

**Locust, not k6:** pure Python, matches the `uv`-first tooling, scripts as
ordinary Python. Lives at `tests/load/locustfile.py` — outside `tests/unit/` and
not named `test_*.py`, so pytest never collects it, and `locust` is **not** a
locked dependency (installed transiently per run). Two modes, the load-testing
analogue of ADR 0015's opt-in split and for the same reason (a real call costs
money and latency):

1. **Primary, cost-free** — against a `test`-profile container: the scripted
   `LLMClient` echoes, real Postgres/Redis/Qdrant contention and real HTTP
   concurrency. Drives pool tuning, the fail-open observation, breaker mechanics.
2. **Secondary, opt-in, billable** — against a `dev`-profile container with a real
   key, small and short, for real end-to-end latency and to trip the breaker
   against the real client. **Never in CI.**

Neither mode is a CI gate — consistent with the opt-in-layer precedent, not a new
exception. Commands: `tests/load/README.md`.

### 2. Chaos testing — a manual runbook, not an automated job

For Postgres, Redis and Qdrant: `docker compose stop`/`start` mid-run under the
primary Locust load, observe, recover. Expected behaviour per store is **already
specified by earlier ADRs**; this stage proves it under concurrency, it does not
redefine it. Full runbook: `docs/runbooks/chaos-testing.md`. In particular the
Redis rate-limiter fail-open (ADR 0008/0019) is **proven and instrumented, not
reversed** — that decision is not reopened here.

### 3. Circuit breaker around `LLMClient` — above the SDK's retries, not instead

A generic primitive (`shared/resilience.py`, cross-cutting infra so it does not
import `services/`) wrapped by `CircuitBreakingLLMClient` (`services/orchestrator/
llm.py`), which implements the same `LLMClient` protocol so `AgentGraph` takes it
as a drop-in. Wired in `services/api/app._build_engine`, the one serving
consumer of `build_llm_client` (left off the factory itself, whose contract is
"the raw client the profile may use").

- **Trips on transport/5xx only:** `anthropic.APIConnectionError` (covers
  `APITimeoutError`, its subclass) and `anthropic.InternalServerError`, verified
  against the pinned `anthropic==0.116.0` hierarchy. **A 400 does not trip it** —
  the "no sampling params" rejection (ADR 0006) is a caller bug, and opening the
  breaker over it would take the endpoint down for a request-shape mistake.
- **Does not touch the SDK's own retry config.** The SDK retries an individual
  call; the breaker stops the *flood* of calls once retries are clearly not
  helping. Both mechanisms exist.
- **Config (`Settings`):** `circuit_breaker_failure_threshold=5` (rides out an
  isolated blip — the SDK already retries transient errors — but trips well before
  a sustained outage ties up many requests each waiting a full
  `ANTHROPIC_TIMEOUT_SECONDS`), `circuit_breaker_cooldown_seconds=30.0` (same
  order as one call's timeout budget — long enough not to hammer a down provider,
  short enough to notice recovery within ~30s).
- **When open:** fail fast with `503 provider_unavailable` — a **new error type**,
  distinct from the 401/429 shapes, via a dedicated handler in
  `services/api/errors.py`. Not a bare 500, and not a hang on the SDK timeout.
- **Hermetic coverage:** a `FaultInjectingLLMClient` double (`tests/fakes.py`)
  raises N times then succeeds; `test_resilience.py`/`test_llm.py` drive
  closed → open → fail-fast → half-open → closed with a controllable clock. The
  real `AnthropicClient` is never exercised — same hermetic posture as ADR 0009.

### 4. Prompt caching — inside `AnthropicClient.stream` only

The system prompt and tool specs are static within a run and usually across a
conversation's turns. `AnthropicClient.stream` marks both with a `cache_control`
breakpoint: the plain `system: str` becomes Anthropic's one-block content form
with `{"type": "ephemeral"}`, and the **last** tool spec is marked (one breakpoint
caches the whole static tool prefix). Contained entirely in that class — the
`LLMClient` wire shape (`system: str`, `tools: Sequence[dict]`) does not change,
and no call site outside it moves. Verified against `anthropic==0.116.0`:
`cache_control` is a **GA first-class field** on `TextBlockParam`/`ToolParam`, **no
beta header** required. The marking copies rather than mutates the caller's dicts
(the registry's tool specs are shared and long-lived).

**Test split:** a hermetic spy on the SDK transport asserts the request *shape*
carries the markers (`test_llm.py`); an actual cache hit
(`cache_read_input_tokens` > 0 on a repeat call) is observable only against the
real API and is folded into the opt-in contract test (ADR 0015), never a new CI
assertion.

### 5. Context compaction — deterministic windowing, not summarization

When the message list the model would see exceeds `context_window_messages`
(default **40** ≈ 20 turns, comfortably inside the model context while bounding
growth), `window_messages` (a plain `@traced` helper `AgentGraph._reason` calls —
not a node, so it can be traced) drops the oldest turns **from the outbound call
only**. `AgentState["messages"]` and what `conversations.py` persists to Postgres
are **untouched**. The window is trimmed forward to start on a genuine user turn
(a `{"role": "user", "content": <str>}`) so a `tool_result` is never sent without
its `tool_use`; if no such boundary exists in the tail (one very long tool loop),
the full list is returned rather than emit a broken request — correctness beats
the size cap.

**Summarization is explicitly rejected:** it is a second model call with its own
cost, latency and failure mode, and a windowed conversation is honestly presented
(older context is gone, not silently and lossily compressed). Noted as possible
future work if the message-count proxy proves wrong once real load data exists.

### 6. Pool tuning — data-driven, reviewed against the harness

`db_pool_max_size` / `redis_pool_max_connections` / `qdrant_pool_max_connections`
and the ADR 0008 `conversation_cache_ttl_seconds` (300s) are the knobs the primary
Locust mode is meant to tune: sweep concurrency, watch where each pool queues or
times out, set the default from that. The TTL only trades memory for hit-rate (a
miss degrades to a Postgres read, never an error), so it cannot cause a
correctness failure under load. See the stage summary for exactly which numbers
were observed versus left at their Stage-5 starting points this session.

### 7. OTel metrics pipeline — spanmetrics/servicegraph, additive

`spanmetrics` + `servicegraph` connectors and a `prometheus` exporter are added to
`otel-collector/config.yml`; Prometheus scrapes the collector's `:8889` as an
**additional** target (the app's `/metrics` scrape is untouched); the Tempo
datasource flips `serviceMap`/`nodeGraph` on. This is a **different signal** — RED
metrics *derived from spans*, purely to drive the service-map/node-graph views —
**not** a second copy of the app's own RED metrics, which is the duplication ADR
0016 refused. It builds the thing ADR 0016 named as future work; it does not
reopen the decision. Still hermetic: dev/prod container config only, `test` never
reaches the collector.

### 8. SLOs — targets to alert against, plus two new rules

No production traffic, so an SLO here is a stated target backed by a Grafana alert,
same symptom-level style as Stage 5's three (which are left **unchanged**). Written
in `docs/slo.md`. Two new rules (`alerting/reliability-rules.yml`), each firing on
a counter of edges on the app's own `/metrics` (`shared/metrics.py`):

1. **`reliability-circuit-breaker-open`** — `increase(circuit_breaker_opened_total)
   > 0`, `for: 1m`, **critical**: an open breaker is a caller-visible new failure
   mode Stage 5's rules do not cover.
2. **`reliability-rate-limiter-fail-open`** — `increase(rate_limiter_fail_open_total)
   > 0`, `for: 5m`, **warning**: instruments the fail-open surface (the decision to
   keep failing open stands; this makes it visible).

## Consequences

- The paid endpoint now fails fast and visibly when the provider is down, cheaper
  per turn (prompt caching) and bounded for long conversations (windowing),
  without any protocol or wire-format change — the `CompletionEngine`/`LLMClient`
  seams held again.
- Two new `Settings` knobs each for the breaker and one for the window; the pool
  knobs are unchanged in shape.
- **Out of scope, restated as still-deferred:** per-conversation concurrency
  control (ADR 0008's other gap — **not** in the Stage 9 row; no row-locking or
  optimistic versioning built here) and LLM-based summarization (rejected above).
- The rate-limiter fail-open (ADR 0019) and the traces-only-for-*app*-RED-metrics
  stance (ADR 0016) are **unchanged** — this stage instruments the first and adds
  a differently-sourced pipeline alongside the second.

## Addendum 2 (2026-07-24) — running the test-profile stack for Locust Mode 1

**Problem (found running Mode 1 for the first time).** The base `docker-compose.yml`
interpolates the auth/provider secrets from root `.env` via `${VAR:-}` **at the
compose layer**, before the app runs, and injects them as real container OS env
vars. OS env is the highest-precedence config tier, so the app's "the `test` profile
ignores root `.env`" logic (ADR 0003) never applies — under `ENVIRONMENT=test
docker compose up` the container still authenticated with root `.env`'s **dev**
`API_KEYS`, and every Locust chat request 401'd. This defeated `tests/load/README.md`
Mode 1's "no `LOAD_TEST_API_KEY` needed" promise. (Separate from and deeper than the
`ENVIRONMENT: dev` literal fixed in addendum 1 — parameterising `ENVIRONMENT`
selected the test profile, but the secrets still arrived from root `.env`.)

**Decision — a `docker-compose.test.yml` override that sets the auth vars to
literal test values** (option 1 in the brief), invoked as
`docker compose -f docker-compose.yml -f docker-compose.test.yml up`. It pins
`ENVIRONMENT=test`, sets `API_KEYS`/`API_KEY_HASH_SECRET` to
`config/environments/test.env`'s fixed test-principal fixture, and pins the two
provider keys empty (the `test` profile constructs no real provider client, ADR
0009/0011, so a real host key must not be baked into a throwaway load container).

**Why literal values, not the two cleaner-looking alternatives** (both empirically
tested with `docker compose config`):

- **Null / passthrough form (`API_KEYS:`)** — rejected: docker compose loads root
  `.env` into the environment it uses for the passthrough form too, so `API_KEYS`
  still resolved to the **dev** value. It does not remove the leak.
- **`--env-file config/environments/test.env`** — rejected: it fixes `API_KEYS`,
  but the host **shell** overrides compose interpolation, so a shell-exported key
  (here `ANTHROPIC_API_KEY`) still leaked a real credential into the container, and
  a shell-exported `API_KEYS` would re-break auth.
- **A literal value in `environment:`** wins over **both** the shell and root
  `.env` — the only form that *guarantees* the fixed test key with zero host-side
  setup, which is exactly Mode 1's promise.

**Trade-off accepted:** the literal duplicates test.env's fixed fixture, a drift
risk. Guarded by a hermetic pytest (`tests/unit/test_load_profile_compose.py`) that
fails if the override and `test.env` disagree — it runs in CI where `docker compose
up` cannot. The *runtime* proof (the container really loads the test key and a chat
request returns 200) is a documented manual check in `tests/load/README.md`, since a
unit test cannot reach docker-compose. The chaos runbook's setup was updated to the
same override (via `COMPOSE_FILE`).
