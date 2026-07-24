# Load testing (Locust) — Stage 9, ADR 0020

Load and chaos testing need a **running stack**. They are opt-in and **never a CI
gate**, exactly like the live-datastore / live-Qdrant / live-provider tests that
already skip by default. `locust` is **not** a locked dependency — it is installed
transiently for a run, so the frozen `uv sync` install stays clean and pytest
never collects `locustfile.py` (wrong directory, wrong filename).

## Why Locust (decided before Stage 9)

Pure Python, matches the project's `uv`-first tooling, and scripts as ordinary
Python rather than a second language's test DSL. See ADR 0020.

---

## Mode 1 — primary, cost-free (against the `test`-profile stack)

The scripted `LLMClient` echoes, so **there is no model bill**. This is what
drives pool tuning, the rate-limiter fail-open observation, and the circuit-breaker
mechanics — none of which need a real model response, only real datastore
contention and real HTTP concurrency.

```bash
# 1. Bring up the full stack in the test profile. The test override is REQUIRED
#    (not just `ENVIRONMENT=test docker compose up`): it forces the api container
#    to use config/environments/test.env's fixed test-principal key instead of
#    root .env's dev keys, which compose would otherwise inject as OS env vars
#    (highest precedence) and every chat request would 401. See ADR 0020.
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build

# 2. Run Locust headless. The default key is the test profile's fixed fake key,
#    so no LOAD_TEST_API_KEY is needed here.
uv run --with locust locust \
  -f tests/load/locustfile.py \
  --host http://localhost:8000 \
  --users 50 --spawn-rate 5 --run-time 3m \
  --headless --only-summary
```

> **Every `docker compose` command for Mode 1 needs both `-f` files** — e.g.
> `docker compose -f docker-compose.yml -f docker-compose.test.yml logs -f api`.

- `--users` is the concurrency knob to sweep for pool tuning (try 25 / 50 / 100
  and watch where a pool queues or times out — record the level in the stage
  summary and set the default in `shared/config.py` from it).
- Open Grafana at <http://localhost:3001> during the run to watch the RED panels
  and (Stage 9) the Tempo service map / node graph now that spanmetrics back them.

### Manual verification that Mode 1 uses the test key (do this once)

A unit test cannot reach docker-compose, so this is a **verification-log-style
manual check**, not pytest. (The hermetic drift guard that the override stays in
sync with `test.env` *is* pytest: `tests/unit/test_load_profile_compose.py`.)

```bash
# (a) Daemon-free: prove compose resolves the TEST key, not root .env's dev key.
docker compose -f docker-compose.yml -f docker-compose.test.yml config \
  | grep -E 'ENVIRONMENT:|API_KEYS:|API_KEY_HASH_SECRET:'
#   expect: ENVIRONMENT: test
#           API_KEYS: test-principal:231965b3…          (from test.env, NOT dev)
#           API_KEY_HASH_SECRET: test-pepper-not-a-real-secret

# (b) Runtime: with the stack up, the container's env carries the test key…
docker compose -f docker-compose.yml -f docker-compose.test.yml exec api \
  sh -c 'echo "$API_KEYS"'          # -> test-principal:231965b3…

# (c) …and the fixed test key authenticates (200, not 401):
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'Authorization: Bearer test-key-not-a-real-secret' \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"ping"}]}' \
  http://localhost:8000/v1/chat/completions          # -> 200
```

## Mode 2 — secondary, opt-in, **billable** (against a `dev`-profile container)

Real Anthropic calls → **real latency and real cost**. Keep it **short and small**,
and **never run it in CI**. This is the only way to observe a true end-to-end
latency number for the SLOs and to trip the breaker against the real client.

```bash
# 1. Mint a key for the run (prints the raw key once + the API_KEYS hash pair).
API_KEY_HASH_SECRET=<pepper> uv run python scripts/generate_api_key.py loadtest

# 2. Boot a dev-profile stack with a real key and the minted API_KEYS pair
#    (ANTHROPIC_API_KEY + API_KEYS + API_KEY_HASH_SECRET come from the OS env).
ENVIRONMENT=dev docker compose up -d --build

# 3. Run a deliberately small, short load with the raw key exported.
LOAD_TEST_API_KEY=<raw-key-from-step-1> \
  uv run --with locust locust \
  -f tests/load/locustfile.py \
  --host http://localhost:8000 \
  --users 3 --spawn-rate 1 --run-time 60s \
  --headless --only-summary
```

Record a **redacted** cost/latency summary in the stage summary — never paste the
raw key or any output that contains it.

**Tripping the breaker against the real client (ADR 0020):** point the dev
container's `ANTHROPIC_API_KEY`/base URL at a dead endpoint (or sever the network
path) and confirm the endpoint returns `503 provider_unavailable` fast rather than
hanging on the SDK timeout, and that `circuit_breaker_opened_total` increments. If
that is not practical, the hermetic fault-injection tests in
`tests/unit/test_llm.py` / `tests/unit/test_resilience.py` are the stated fallback.

---

## Chaos testing

The datastore stop/start runbook is a separate document:
[`docs/runbooks/chaos-testing.md`](../../docs/runbooks/chaos-testing.md). Run it
**against the Mode 1 stack under Locust load**, so the failure and recovery are
observed under real concurrency, not a single curl.
