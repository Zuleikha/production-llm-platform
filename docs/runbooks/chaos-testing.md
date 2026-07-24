# Runbook — datastore chaos testing

**Stage 9, ADR 0020.** A **manual** runbook, not an automated CI job — chaos
testing needs a running stack, the same reason the live-datastore tests skip by
default. Its job is to **prove the failure behaviour each earlier ADR already
specified, under real concurrent load** — not to invent new behaviour. Run every
step against the **primary (cost-free) Locust stack** so the failure and recovery
are observed under concurrency, not a single curl.

> The expected behaviour per store is **already decided** by earlier ADRs. This
> runbook does not change any of it. In particular the Redis rate-limiter
> fail-open is **proven and instrumented here, not reversed** (ADR 0008/0019).

## Setup

```bash
# Full stack, test profile (echoing model, real datastores). The test override is
# REQUIRED so the api container uses test.env's fixed key, not root .env's dev key
# (ADR 0020). Export COMPOSE_FILE once so every `docker compose ...` below (stop,
# start, logs) targets both files without repeating -f. NOTE: the path separator is
# `:` on POSIX and `;` on Windows/PowerShell.
export COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml
docker compose up -d --build

# Drive load in another terminal (see tests/load/README.md).
uv run --with locust locust -f tests/load/locustfile.py \
  --host http://localhost:8000 --users 50 --spawn-rate 5 --run-time 10m \
  --headless --only-summary

# Watch, in a third terminal:
watch -n1 'curl -s -o /dev/null -w "ready:%{http_code}\n" localhost:8000/ready'
docker compose logs -f api            # structured JSON events
# Grafana http://localhost:3001 — RED panels + the fail-open / breaker counters.
```

Each scenario is: observe healthy → `docker compose stop <svc>` mid-run → observe
degraded → `docker compose start <svc>` → confirm recovery.

---

## Scenario 1 — Postgres down

```bash
docker compose stop postgres
# ... observe ...
docker compose start postgres
```

**Expected (ADR 0005/0007):**

| Signal | Expectation |
|--------|-------------|
| `GET /ready` | **503 `not_ready`**, JSON names `postgres` unavailable |
| Chat with a `conversation_id` | fails **loudly** (history load hits a dead pool) — a 5xx, not a wrong answer |
| Stateless chat (no `conversation_id`) | still succeeds — it touches no datastore |
| Container | **no crash loop** — `startup()` never raises (ADR 0005); the pod stays up and un-ready |
| Recovery | after `start`, `/ready` returns to 200 without an api restart |

## Scenario 2 — Redis down

```bash
docker compose stop redis
# ... observe ...
docker compose start redis
```

**Expected (ADR 0008/0019) — the fail-open surface Stage 9 owns:**

| Signal | Expectation |
|--------|-------------|
| `GET /ready` | **503 `not_ready`**, names `redis` |
| Conversation history | **degrades to a Postgres read** — the one sanctioned fail-loud exception (ADR 0008); answers still correct, just uncached |
| Rate limiter | **fails open + logs** `ratelimit.degraded` **every time**, and now increments `rate_limiter_fail_open_total` (ADR 0020) — requests are allowed uncapped |
| Alert | `reliability-rate-limiter-fail-open` fires after its `for: 5m` |
| Recovery | after `start`, caching and counting resume; no api restart |

**This is the point of the scenario:** prove fail-open under real concurrent load
(Stage 8 only checked it with a single curl), and confirm it is now *visible*
(metric + alert), **not reversed**.

## Scenario 3 — Qdrant down

```bash
docker compose stop qdrant
# ... observe ...
docker compose start qdrant
```

**Expected (Stage 4 wiring):**

| Signal | Expectation |
|--------|-------------|
| `GET /ready` | **503 `not_ready`**, names `qdrant` |
| Chat | **still answers** — without `document_search`. The retrieval tool is only wired in `with_tools` when Qdrant is up, so a Qdrant outage degrades to ungrounded answers, it does **not** fail the whole chat endpoint |
| Citations | empty on affected turns (nothing retrieved), not an error |
| Recovery | after `start`, `document_search` is available again on the next engine build / restart |

> Note the known limitation (ADR 0012): the retrieval tool set is composed at
> engine-build time. A Qdrant that comes back mid-process may need the api to
> rebuild its engine (restart) before `document_search` re-enables — recovery of
> `/ready` is immediate, recovery of the *tool* may not be.

## Scenario 4 — Anthropic unreachable (circuit breaker)

The real provider is **never** deliberately taken down. This path is covered by:

- the **hermetic fault-injection tests** (`tests/unit/test_llm.py`,
  `tests/unit/test_resilience.py`) — closed → open → half-open → closed; and
- optionally, the **secondary billable run** pointed at a dead endpoint
  (`tests/load/README.md`), confirming `503 provider_unavailable` fast + the
  `reliability-circuit-breaker-open` alert.

**Expected (ADR 0020):** after `CIRCUIT_BREAKER_FAILURE_THRESHOLD` consecutive
transport/5xx failures the breaker opens; chat fails fast with `503
provider_unavailable` (a distinct type from 401/429) for the cooldown; one
half-open trial then closes it on success. A 400 never trips it.

---

## Recording results

Capture, for each scenario actually run: the exact commands, the observed
`/ready` status code, the actual log line(s) (`ratelimit.degraded`,
`migration.*`, `circuit_breaker.state_change`, …), and which Grafana panel moved.
Put the observations in the stage summary — **what was observed**, not a paraphrase
of this table.
