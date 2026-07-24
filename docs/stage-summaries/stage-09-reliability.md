# Stage 9 — Reliability — self-report

> **What this is.** CC's own build report. Independent manual verification is
> logged separately at `docs/verification-log/stage-09-reliability.md`. This
> states **only what was directly run and observed this session** — prior-stage
> claims are not re-verified here.
>
> **Environment constraint stated up front (it shapes several sections below):**
> **Docker Desktop's daemon was not running this session** (`docker info` → down).
> Every deliverable that needs a running stack — the container-boot check, the
> primary/secondary Locust runs and their pool-tuning numbers, and the chaos
> runbook execution — **could not be run this session** and is reported as such,
> not paraphrased or invented. The full hermetic Python gate, which needs no
> stack, ran and is green. The circuit breaker is proven by the hermetic
> fault-injection tests (the stated fallback, ADR 0020 / stage prompt §Testing).

## What was implemented, file by file

**New source**

- `shared/resilience.py` — generic `CircuitBreaker` (`CLOSED`/`OPEN`/`HALF_OPEN`,
  `StrEnum`), `CircuitBreakerOpenError`, injectable clock. Trips only on a
  configured `trip_on` exception set; a non-qualifying exception leaves state
  untouched. `before_call`/`record_success`/`record_failure` are `@traced`; state
  transitions log `circuit_breaker.state_change` (name + from/to only, no call
  data) and increment the open counter. No `services/` import (cross-cutting infra).
- `shared/metrics.py` — two cross-cutting Prometheus counters raised below the
  `api` layer: `circuit_breaker_opened_total{name}` and
  `rate_limiter_fail_open_total`. Kept in `shared/` (not `services/api/metrics.py`)
  because `orchestrator`/`security` sit below `api` and must not import it.

**Changed source**

- `services/orchestrator/llm.py` — `PROVIDER_DOWN_ERRORS = (APIConnectionError,
  InternalServerError)` (verified against `anthropic==0.116.0`); `_with_cache_control`
  (system → cached content block, last tool spec marked, copies not mutates);
  prompt-caching applied inside `AnthropicClient.stream` only; `CircuitBreakingLLMClient`
  wrapper (same protocol, records success/failure, re-raises non-qualifying);
  `build_resilient_llm_client` factory. `build_llm_client` unchanged (still returns
  the bare client — its tripwire test still passes).
- `services/orchestrator/graph.py` — `window_messages` (`@traced` plain helper) and
  `AgentGraph(context_window_messages=...)`; `_reason` windows the **outbound** call
  only.
- `services/api/app.py` — `_build_engine` wraps the client via
  `build_resilient_llm_client` and passes `context_window_messages`.
- `services/api/errors.py` — `circuit_breaker_open_handler` → `503
  provider_unavailable`, registered before the catch-all.
- `services/security/rate_limit.py` — increments `RATE_LIMITER_FAIL_OPEN` on both
  fail-open branches.
- `shared/config.py` — `circuit_breaker_failure_threshold` (5),
  `circuit_breaker_cooldown_seconds` (30.0), `context_window_messages` (40); Stage-9
  review comments on the pool sizes + cache TTL.

**Infra config** (dev/prod only; `test` never reaches the collector)

- `infrastructure/docker/otel-collector/config.yml` — `spanmetrics` + `servicegraph`
  connectors, `prometheus` exporter on `:8889`, new `metrics/spanmetrics` pipeline;
  traces pipeline now fans out to `[otlp/tempo, spanmetrics, servicegraph]`.
- `infrastructure/docker/prometheus/prometheus.yml` — new
  `otel-collector-spanmetrics` scrape target (the `api` scrape is untouched).
- `infrastructure/docker/grafana/provisioning/datasources/tempo.yml` — `serviceMap`
  + `nodeGraph` enabled.
- `infrastructure/docker/grafana/provisioning/alerting/reliability-rules.yml` — the
  two new alert rules (Stage 5's `api-rules.yml` untouched).

**Tests** — `tests/unit/test_resilience.py` (new); additions to
`tests/unit/test_llm.py`, `test_graph.py`, `test_security.py`, `test_chat.py`;
`FaultInjectingLLMClient` in `tests/fakes.py`.

**Load/docs** — `tests/load/locustfile.py` + `tests/load/README.md`;
`docs/runbooks/chaos-testing.md`; `docs/slo.md`; `docs/adr/0020-…md`;
`docs/architecture.md` new Reliability section (+ regenerated `architecture.html`).

**Config** — `pyproject.toml`: mypy `exclude = ["^tests/load/"]` (Locust is not a
locked dep; installed transiently per run).

## CLAUDE.md line count + deferred-table

- **Before: 150 lines. After: 150 lines** (at the cap; trimmed verbose bullets to
  make room for the Reliability convention bullet and Stage-9 current-state).
- Deferred table updated: the Stage-9 row's built items (load/chaos, SLOs, OTel
  metrics, pool tuning, circuit breaking, prompt caching, context compaction) are
  removed; what was **not** built is restated as still-deferred —
  **per-conversation concurrency control** (ADR 0008 gap, explicitly out of Stage-9
  scope), **LLM summarization** (windowing chosen instead), and **Voyage circuit
  breaking** (only the Anthropic call is wrapped).

## Circuit breaker — trip conditions, values, hermetic results

- **Trips on:** `anthropic.APIConnectionError` (covers `APITimeoutError`, its
  subclass) and `anthropic.InternalServerError` — verified against the installed
  `anthropic==0.116.0` MRO. **Does not trip on a 400** (`BadRequestError`): a
  hermetic test asserts a 400 raised repeatedly leaves the breaker `CLOSED`.
- **Values:** `failure_threshold=5` (rides out an isolated blip — the SDK already
  retries transient errors — but trips before a sustained outage ties up many
  requests each waiting a full 60s timeout); `cooldown_seconds=30.0` (same order as
  one call's timeout budget: long enough not to hammer a down provider, short
  enough to notice recovery within ~30s).
- **Hermetic results (ran, green):** `test_resilience.py` drives
  closed → (threshold) → open → fail-fast → (cooldown) → half-open → success →
  closed with a hand-cranked clock, plus success-resets-count, non-qualifying-
  ignored, re-open-on-failed-trial, and the open counter incrementing once per
  edge. `test_llm.py::TestCircuitBreakingLLMClient` proves the wrapper passes
  through when closed, fails fast without touching the inner client when open,
  recovers via the half-open trial, and does not trip on a 400.
  `test_chat.py::test_open_circuit_breaker_returns_503_provider_unavailable`
  asserts the full-app 503 envelope with `type: "provider_unavailable"`. **The real
  `AnthropicClient` is never exercised** (ADR 0009 posture intact).

## Prompt caching — shape + live-hit status

- **Shape against `anthropic==0.116.0`:** `cache_control` is a **GA first-class
  field** on `TextBlockParam`/`ToolParam` (`{"type": "ephemeral"}`), **no beta
  header required** (confirmed by introspecting the SDK's param types). The system
  string is sent as one cached content block; the **last** tool spec is marked (one
  breakpoint caches the whole static tool prefix). Caller specs are copied, not
  mutated. Hermetic spy tests assert exactly this request shape.
- **Live cache hit:** **not observed this session.** A real hit
  (`cache_read_input_tokens` > 0 on a repeat call) is only confirmable against the
  live API and is folded into the opt-in contract test (ADR 0015) — which was **not
  run** (no billable call was authorised, and the dev stack could not boot). Stated,
  not claimed.

## Context compaction — window + persistence

- **Window: 40 messages** (`context_window_messages`, ≈ 20 turns) — comfortably
  inside the model context while bounding growth. Windowing is deterministic and
  applies to the **outbound model call only**; the slice is trimmed forward to
  start on a genuine `{"role":"user","content":<str>}` turn so a `tool_result` is
  never sent without its `tool_use` (if no such boundary exists in the tail, the
  full list is sent — correctness over the cap).
- **Persistence confirmed (hermetic test):**
  `test_graph.py::TestContextCompaction::test_full_history_is_retained_in_state`
  asserts `state["messages"]` keeps every input turn plus the new one while the
  scripted client received only the windowed slice. `AgentState["messages"]` /
  `conversations.py` are untouched — only the model call is windowed.

## Pool-tuning numbers

| Pool / knob | Old default | New default | Concurrency at which old showed queuing/timeout |
|-------------|-------------|-------------|-------------------------------------------------|
| `db_pool_max_size` | 10 | **10 (unchanged)** | **not measured — Docker down** |
| `redis_pool_max_connections` | 10 | **10 (unchanged)** | **not measured — Docker down** |
| `qdrant_pool_max_connections` | 10 | **10 (unchanged)** | **not measured — Docker down** |
| `conversation_cache_ttl_seconds` | 300 | **300 (kept, reasoned)** | n/a — a miss degrades to a Postgres read, never an error; TTL only trades memory for hit-rate |

**Deviation, stated honestly:** the stage wants pool defaults set from an observed
primary-Locust run. That run needs the compose stack, which could not boot this
session. Rather than fabricate numbers, the defaults are **left at their Stage-5
starting points** and the tuning is recorded as an operator step: run
`tests/load/README.md` Mode 1 sweeping `--users 25/50/100` and set each default
from where the pool first queues or times out. The cache TTL is *examined* and
deliberately kept (it cannot cause a correctness failure under load) — an examined
default, not an unexamined one.

## Chaos-runbook commands + observations

**Not executed this session — Docker daemon down.** The runbook
(`docs/runbooks/chaos-testing.md`) specifies, per store, the exact
`docker compose stop/start <svc>` sequence, what to watch (`/ready` status, the
JSON store name, the log line, the Grafana panel), and the expected outcome per the
governing ADR (Postgres → 503 + no crash loop, ADR 0005/0007; Redis → history
degrades to Postgres + rate limiter fails open+logs+counts, ADR 0008/0019/0020;
Qdrant → chat still answers without `document_search`, Stage 4 wiring). **No actual
status codes or log lines were observed** this session; they must be captured when
an operator runs it against a live stack.

## Locust commands + numbers

- **Primary (cost-free), Mode 1:**
  `docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build`
  (see addendum 2 — the plain `ENVIRONMENT=test` form 401s) then
  `uv run --with locust locust -f tests/load/locustfile.py --host http://localhost:8000 --users 50 --spawn-rate 5 --run-time 3m --headless --only-summary`.
  **Not run — Docker down. No throughput/latency numbers observed this session.**
- **Secondary (opt-in, billable), Mode 2:** dev-profile stack + a minted key +
  `LOAD_TEST_API_KEY=<raw> … --users 3 --spawn-rate 1 --run-time 60s`. **Not run —
  Docker down, and no billable call was authorised.** No real end-to-end latency
  numbers were obtained. (A real `ANTHROPIC_API_KEY` is present in the environment,
  but spending money requires explicit confirmation and a running dev stack, so no
  call was made.)
- **Breaker vs. the real client:** **not run** (needs the dev stack). Per the stage
  prompt this falls back to the hermetic fault-injection tests above, which is the
  stated and sufficient coverage of the mechanics.

## The two new Grafana alert rules (Stage 5 style)

| UID | Title | Expr (5m) | `for` | Severity | Why |
|-----|-------|-----------|-------|----------|-----|
| `reliability-circuit-breaker-open` | Circuit breaker open | `sum(increase(circuit_breaker_opened_total{job="api"}[5m])) > 0` | 1m | critical | Open breaker = chat failing fast with 503; a new caller-visible failure Stage 5's rules miss |
| `reliability-rate-limiter-fail-open` | Rate limiter failing open | `sum(increase(rate_limiter_fail_open_total{job="api"}[5m])) > 0` | 5m | warning | Redis down → requests uncapped; instrument the fail-open surface, don't reverse it |

**Stage 5's three originals are unchanged** — they live in the untouched
`api-rules.yml`; these two are a separate `reliability-rules.yml` file.

## Python quality gate (ran this session)

- **pytest: 391 passed, 9 skipped** (the 9 are the pre-existing opt-in live layers).
  **+29 over the Stage 8 baseline of 362.**
- **ruff check: All checks passed.**
- **ruff format --check: 84 files already formatted.**
- **mypy (strict): Success, no issues in 84 source files** (`tests/load/` excluded —
  Locust is not a locked dep).
- **gitleaks / pip-audit: not run locally** (not on PATH / not in the venv this
  session; they run in CI). The one fake credential added
  (`test-key-not-a-real-secret` in the locustfile) matches the `.gitleaks.toml`
  `not-a-real` allowlist convention, so it will not trip the CI scan.

## architecture.md + regeneration

- `docs/architecture.md` gained a **Reliability (Stage 9, ADR 0020)** section
  (circuit breaker, prompt caching, context compaction, the metrics pipeline) and
  two new `shared/` rows; the Planned table, agent-stack non-goals and See-also list
  were updated. Regenerated with **`uv run python scripts/build_architecture.py`**;
  `--check` reports "architecture.html is up to date" and
  `tests/unit/test_architecture.py` passes (14). The HTML was never hand-edited.

## architecture-history close-out

Stage 9's row + caption were added to `_STAGES`/`_CAPTIONS` in
`docs/architecture-history/generate.py` and the generator was started. **This folder
is gitignored and was NOT committed or `git add`ed.** Because no Stage-9 completion
commit exists yet, the generator snapshots the working-tree `architecture.md` and
**self-heals to the git blob once the commit lands** (per
`docs/architecture-history/README.md`) — so a re-run after the commit is the
authoritative pass.

## Deviations from scope

1. **Docker-dependent runs not executed** (container boot in both profiles, primary
   & secondary Locust, chaos runbook) — the daemon was down this session. Documented
   throughout rather than faked; the circuit breaker relies on the hermetic
   fault-injection tests as the stage-sanctioned fallback.
2. **Pool defaults left unchanged** (see above) — no observed load data to set them
   from; recorded as an operator step with the exact command.
3. **No billable call made** — the secondary Locust run and the live prompt-cache-hit
   check both require spending money and a dev stack; neither was authorised/possible.
4. `pyproject.toml` gained a mypy `exclude` for `tests/load/` — a necessary
   consequence of Locust not being a locked dependency (itself the decided design),
   not a scope change.

## Known limitations / deliberately deferred

- **Per-conversation concurrency control** — ADR 0008's other known gap, explicitly
  out of Stage-9 scope (not in the deferred-table row); no row-locking or optimistic
  versioning was built. Two concurrent turns on one `conversation_id` still collide
  loudly.
- **LLM-based summarization** — rejected (ADR 0020): it is a second billable model
  call with its own latency/failure mode. Deterministic windowing is used; a
  windowed conversation honestly loses its oldest turns rather than lossily
  compressing them. Revisit only if the message-count proxy proves wrong against
  real load data.
- **Circuit breaking around the Voyage call** — only the Anthropic call is wrapped.
  A Voyage outage already degrades to ungrounded answers (Stage 4 wiring), so it is
  a lower-priority reliability surface; left deferred.
- **Streaming-path breaker envelope** — an open breaker on the SSE path terminates
  the stream rather than rendering the 503 envelope (the exception is raised inside
  the streaming generator, after the response has started). The non-streaming path
  renders the clean 503. Acceptable and noted; the breaker still fails fast either
  way.
- **Pool numbers are unvalidated best-guesses** until the primary Locust run is
  executed against a live stack (see the pool-tuning section).

---

## Addendum — 2026-07-24: two bugs from independent verification

Independent verification ran the stack (which the build session could not — Docker
was down) and found **two bugs**, both now fixed with hermetic regression coverage.
Full quality gate re-run after the fixes: **ruff clean · ruff-format clean · mypy
strict clean (84 files) · pytest 392 passed / 9 skipped** (+1 test over this
stage's 391, i.e. +30 over the 362 baseline).

### Bug 1 — `docker-compose.yml` hardcoded `ENVIRONMENT: dev`

- **What was wrong:** the `api` service set `ENVIRONMENT: dev` as a **literal**, not
  the `${VAR:-default}` interpolation every other env-derived value in the file uses.
  Stage 9's own `tests/load/README.md` Mode 1 instructs
  `ENVIRONMENT=test docker compose up -d --build` for the cost-free primary Locust
  run — but that override was **silently ignored**, so the container always booted
  the `dev` profile. The cost-free load mode (the one this stage leans on for pool
  tuning and the chaos runbook) could never actually engage the `test` profile.
- **Fix:** `ENVIRONMENT: ${ENVIRONMENT:-dev}` (matches the `ANTHROPIC_API_KEY:
  ${ANTHROPIC_API_KEY:-}` pattern already in the file), with a comment noting the
  two-mode Locust harness depends on it. **Verified:** `docker compose config`
  renders `ENVIRONMENT: dev` by default and `ENVIRONMENT: test` under
  `ENVIRONMENT=test docker compose config`. No test needed for compose syntax.

### Bug 2 — unhandled 500s leaked a raw traceback in the dev profile

- **What was wrong:** `create_app` passed `debug=settings.debug` to `FastAPI(...)`,
  and `config/environments/dev.env` sets `DEBUG=true`. FastAPI forwards `debug` to
  Starlette's `ServerErrorMiddleware`, which **with `debug=True` returns a raw
  traceback response and bypasses the registered catch-all `Exception` handler**.
  So any exception that was neither an `HTTPException` nor a
  `CircuitBreakerOpenError` — reproduced with an invalid `ANTHROPIC_API_KEY` giving
  `anthropic.AuthenticationError` (not in `PROVIDER_DOWN_ERRORS`, so the breaker
  correctly passes it through) — returned a **Python stack trace in the HTTP
  response body**, violating the standing "500s never leak internals" rule
  (CLAUDE.md). Only the *generic* 500 leaked: the breaker's 503 and every
  `HTTPException` render via `ExceptionMiddleware`, a different middleware that
  `debug` does not affect — which is why Stage 9's 503 path looked fine.
- **Fix:** `create_app` now hardcodes `debug=False` for the ASGI app, with a comment
  explaining why it must never be wired to `settings.debug`. The full trace still
  goes to the server log via `unhandled_exception_handler` (`exc_info=exc`), and the
  response is the standard `{"error": {"type": "internal_error", …}}` envelope with
  no traceback. `settings.debug` is untouched and still drives uvicorn `--reload` in
  `services/api/__main__.py` (its only other use).
- **Regression test:**
  `tests/unit/test_chat.py::test_non_qualifying_provider_exception_renders_uniform_500_envelope`
  builds the app with `debug=True` (the trigger), injects a
  `CircuitBreakingLLMClient` over a fault-injecting client that raises
  `anthropic.AuthenticationError`, and asserts the response is a `500`
  `internal_error` envelope with **no `Traceback` and no exception type name** in the
  body. **Verified both ways:** it passes with the fix, and fails on the exact leak
  (`assert 'Traceback' not in resp.text`) when `debug=settings.debug` is temporarily
  restored.

### Scope of the fix

Two files changed for the bugs (`docker-compose.yml`, `services/api/app.py`) plus
one test added (`tests/unit/test_chat.py`). No other Stage 9 behaviour changed; the
circuit breaker, prompt caching, context compaction, metrics pipeline, SLOs and
docs are as originally reported above. Not committed or pushed.

---

## Addendum 2 — 2026-07-24: test-profile container authenticated with dev keys

A second, deeper bug — related to addendum 1's `ENVIRONMENT` hardcode but distinct
— surfaced when independent verification ran the **primary Locust Mode 1 load test
against the test-profile stack for the first time**. Fixed, with a hermetic drift
guard and a documented runtime check. Full gate re-run after this fix: **ruff clean
· ruff-format clean · mypy strict clean (85 source files) · pytest 395 passed / 9
skipped** (+3 over addendum 1's 392: the new compose drift-guard tests).

### The bug

`docker-compose.yml`'s `api` service interpolates `ANTHROPIC_API_KEY`,
`VOYAGE_API_KEY`, `API_KEYS`, and `API_KEY_HASH_SECRET` from root `.env` via the
`${VAR:-}` form — **at the compose layer, before the app runs**. Those land as real
container **OS env vars**, the highest-precedence config tier, so the app's "the
`test` profile ignores root `.env`" logic (shared/config.py) never gets a chance to
apply. Under `ENVIRONMENT=test docker compose up`, the container received root
`.env`'s **dev** `API_KEYS`, not `config/environments/test.env`'s fixed
test-principal key — so **every Locust chat request 401'd**, defeating Mode 1's
"just works, no `LOAD_TEST_API_KEY`" promise. (Addendum 1 parameterised
`ENVIRONMENT` so the *profile* switched; this bug is that the *secrets* still came
from root `.env` regardless of profile.)

### The fix (design decision — see ADR 0020 addendum 2)

A new **`docker-compose.test.yml` override** invoked as
`docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build`. It
pins `ENVIRONMENT=test`, sets `API_KEYS`/`API_KEY_HASH_SECRET` to test.env's fixed
fixture as **literal values**, and pins the provider keys empty.

**Why literal values** (both alternatives were empirically tested with
`docker compose config`):

- **Null/passthrough form** (`API_KEYS:`) — compose still resolved it from root
  `.env` to the dev value. Does not fix the leak.
- **`--env-file config/environments/test.env`** — fixed `API_KEYS`, but the host
  **shell** overrode interpolation and leaked the real `ANTHROPIC_API_KEY` into the
  container.
- **Literal `environment:` value** — wins over **both** the shell and root `.env`;
  the only form that guarantees the fixed test key with zero host setup.

**Verified** with `docker compose -f docker-compose.yml -f docker-compose.test.yml
config`: `ENVIRONMENT: test`, `API_KEYS: test-principal:231965b3…` (the test value,
not dev), `API_KEY_HASH_SECRET: test-pepper-not-a-real-secret`, both provider keys
empty — despite root `.env` and a shell `ANTHROPIC_API_KEY` both being present.

### How it is proved

- **Hermetic pytest (CI-runnable):**
  `tests/unit/test_load_profile_compose.py` parses the override + `test.env` and
  asserts they agree (`API_KEYS`, `API_KEY_HASH_SECRET`), that `ENVIRONMENT=test`,
  and that the provider keys are empty. This guards the **drift risk** of the literal
  values (test.env changing while the override goes stale) — the failure mode that
  would silently reintroduce the 401. It cannot prove the *runtime* behaviour (no
  unit test reaches docker-compose).
- **Documented manual check (verification-log style, not pytest):** added to
  `tests/load/README.md` — (a) daemon-free `docker compose … config | grep` showing
  the test key, (b) `docker compose … exec api sh -c 'echo "$API_KEYS"'`, and (c) a
  `curl` with `Bearer test-key-not-a-real-secret` expecting **200**. Stated
  explicitly as manual because a unit test cannot reach docker-compose.

### Other files touched

- `tests/load/README.md` — Mode 1 command changed to the two-`-f` invocation; a
  "verify Mode 1 uses the test key" section added; noted that every Mode 1
  `docker compose` command needs both files.
- `docs/runbooks/chaos-testing.md` — setup now `export COMPOSE_FILE=…` + `docker
  compose up`, so the whole runbook targets the test override.
- `docs/adr/0020-…md` — addendum 2 records the decision and the two rejected
  alternatives.

No application code changed for this bug (it is purely a compose/config-precedence
issue). Not committed or pushed.

---

## Addendum 3 — 2026-07-24: chaos scenarios 2 & 3 executed under load (observed)

First time the chaos runbook was **run against a live stack** (test profile,
`docker-compose.test.yml` override). A Locust load ran in the background throughout
— `uv run --with locust locust -f tests/load/locustfile.py --host
http://localhost:8000 --users 30 --spawn-rate 5 --run-time 8m --headless` (15
`ChatUser` + 15 `ProbeUser`, all spawned) — so observations are under real
concurrency. Scenario 1 (Postgres) was done in an earlier verification session and
is not re-reported here. **Recorded per the runbook's "Recording results" section:
what was observed, not the expected-behaviour tables.**

### Scenario 2 — Redis down (completion)

Commands (Redis was already stopped at the start of this session):
```
curl -s -w '%{http_code}' -H 'Authorization: Bearer test-key-not-a-real-secret' \
  -d '{"messages":[{"role":"user","content":"chaos scenario 2: redis down"}]}' \
  http://localhost:8000/v1/chat/completions
curl -s http://localhost:8000/metrics | grep '^rate_limiter_fail_open_total'
docker logs production-llm-platform-api-1 --since 90s | grep ratelimit.degraded
docker compose -f docker-compose.yml -f docker-compose.test.yml start redis
```

Observed:

| Signal | Observed |
|--------|----------|
| `/ready` (redis down) | **HTTP 503** `{"status":"not_ready","checks":{"postgres":"ok","redis":"unavailable","qdrant":"ok"}}` |
| Chat with redis down | **HTTP 200** — body `{"…","content":"You said: chaos scenario 2: redis down","finish_reason":"stop"}` — **rate limiter failed OPEN, request served** |
| `rate_limiter_fail_open_total` | **2617.0** at probe time → **2738.0** by end of scenario (pumped by the load while redis stayed down; frozen once redis recovered) |
| `ratelimit.degraded` log | verbatim (WARNING, `logger":"security.rate_limit`, `"reason":"redis_error"`), e.g. `{"timestamp":"2026-07-24T13:18:10.344578+00:00", … "message":"ratelimit.degraded", "reason":"redis_error", "exception":"Traceback … redis.exceptions.MaxConnectionsError: Too many connections"}` |
| `/ready` after `start redis` | **503 → 200 within ~2s** (`t+1s` 503, `t+2s` 200), no api restart |

**Deviation from the runbook's expectation — worth flagging.** The runbook says
Redis-down → "fail open + log". Under **real concurrent load** the underlying redis
exception on the fail-open path was frequently **`redis.exceptions.MaxConnectionsError:
Too many connections`** (and `redis.exceptions.TimeoutError: Timeout connecting to
server`), not a plain refused connection: with redis dead, every concurrent request
holds a pool slot while its connect times out, so the `redis_pool_max_connections`
pool **saturates**. The fail-open still held every time (200s throughout, `reason:
redis_error` caught by the broad `except Exception`), so behaviour is correct — but
a single-curl test would only ever see a clean connection error, never the pool
exhaustion that real concurrency produces. This is exactly the failure texture the
"observe under load, not a single curl" instruction exists to surface.

### Scenario 3 — Qdrant down (full)

Commands:
```
docker compose -f docker-compose.yml -f docker-compose.test.yml stop qdrant
curl -s -w '%{http_code}' http://localhost:8000/ready
curl … -d '{"messages":[{"role":"user","content":"chaos scenario 3: qdrant down…"}]}' \
  http://localhost:8000/v1/chat/completions
docker compose -f docker-compose.yml -f docker-compose.test.yml start qdrant
```

Observed:

| Signal | Observed |
|--------|----------|
| `/ready` (qdrant down) | **HTTP 503** `{"status":"not_ready","checks":{"postgres":"ok","redis":"ok","qdrant":"unavailable"}}` |
| Chat with qdrant down (**under load**) | **HTTP 429** — see the finding below |
| Chat with qdrant down (load quiesced) | **HTTP 200**, `"citations":[]`, `content":"You said: chaos scenario 3: qdrant down, quiesced"` — endpoint serves without `document_search` |
| `/ready` after `start qdrant` | **503 → 200 within ~4s** (`t+2s` 503, `t+4s` 200), no api restart |

**Deviation from the runbook's expectation — worth flagging.** The runbook expects
Qdrant-down → chat **200** with empty citations. Under load the first probe returned
**429, not 200** — a *rate-limit* artifact, not a Qdrant failure: the Locust load
and the manual probe **all authenticate as the single `test-principal`** (the only
key in `test.env`), and once Redis had recovered in Scenario 2 the per-principal
limiter (`RATE_LIMIT_REQUESTS=60` / `60s`) correctly capped the shared traffic. It
had nothing to do with Qdrant. After stopping the load and waiting one 60s window
for the counter to drain, the Qdrant-down chat returned the expected **200 /
`citations:[]`**, confirming the endpoint stays up without retrieval. **Takeaway:**
the load harness is limited by the single test-principal — a faithful multi-client
load test (and any per-client rate-limit observation) needs several principals in
`test.env`; today all virtual users collide on one bucket.

**Caveat on "without `document_search`":** under the `test` profile the scripted
`LLMClient` echoes and never *requests* `document_search`, so this scenario proves
the **chat endpoint stays up when Qdrant is down** (200, empty citations) but does
not exercise a live retrieval-skip — that would need the real model (Mode 2), which
was not run.

### Not observed (correctly)

- `circuit_breaker_opened_total` — **no series present** on `/metrics` (the labelled
  counter is created on first open, and the breaker never opened: Anthropic was not
  taken down, per the runbook, Scenario 4 stays hermetic). Expected.

### Final state

`/ready` **200** `{"postgres":"ok","redis":"ok","qdrant":"ok"}`; a post-recovery
chat returned **200** `citations:[]`. Stack left healthy. Locust load stopped. Not
committed or pushed.
