# Stage 02 — API

| | |
|---|---|
| **Stage** | 2 of 10 |
| **Status** | ✅ Complete |
| **Version** | `0.1.0` |
| **Date** | 2026-07-14 |

---

## 1. Objective

Turn the Stage 1 foundation into a real API surface: a chat completion endpoint
with streaming, genuine datastore connections with pooling and lifecycle
management, and a `/ready` that actually probes its dependencies instead of
returning `checks: {}` unconditionally.

Explicit non-goal: **no LLM is called.** The endpoint is backed by a
deterministic echo engine behind a `CompletionEngine` protocol. The point is to
prove the contract, the validation and the streaming plumbing so Stage 3 can swap
in a real model without touching the routes. The orchestrator remains an
untouched stub.

## 2. Features implemented

| Feature | Detail |
|---------|--------|
| **Chat completion endpoint** | `POST /v1/chat/completions`; OpenAI-shaped request/response; Pydantic validation (≥1 message, role enum, `max_tokens > 0`, `0 ≤ temperature ≤ 2`); mocked usage accounting; `max_tokens` truncation → `finish_reason: "length"` |
| **SSE streaming** | `"stream": true` → `text/event-stream`, `data: {json}` frames, `data: [DONE]` terminator; one id per stream; role on first chunk; `finish_reason` on last; `no-cache` + `X-Accel-Buffering: no` so proxies cannot buffer it (ADR 0004) |
| **Completion engine seam** | `CompletionEngine` protocol + `EchoEngine`; injected via `app.state.engine` and a `get_engine` dependency, so Stage 3 substitutes an orchestrator-backed engine with no route change |
| **Datastore connection layer** | `shared/datastores.py`: `Datastore` ABC + `PostgresDatastore` (asyncpg pool), `RedisDatastore` (redis.asyncio pool), `QdrantDatastore` (pooled `httpx.AsyncClient` → `GET /readyz`) |
| **Registry + lifespan** | `DatastoreRegistry.from_settings`; pools opened on startup, closed on shutdown; startup is **concurrent** and never raises; a failing `close` cannot strand the other pools |
| **Real `/ready` probes** | Probes every configured store concurrently (`asyncio.gather`, each bounded by a timeout); **503** + `{"status":"not_ready","checks":{…}}` when any is `unavailable` |
| **`/health` stays pure liveness** | Touches no datastore; pinned by a tripwire test whose `ping` raises |
| **Fail-loud prod config** | A `Settings` validator requires all three datastore URLs under `prod` — a missing `DATABASE_URL` now fails at boot, naming the variable, instead of reading as `not_configured` and silently passing readiness (ADR 0005) |
| **Hermetic test profile** | The `test` profile ignores the repo-root `.env`, so a developer who copied `.env.example` cannot make the suite dial a live Postgres |
| **CI verifies the real thing** | The docker job now runs postgres/redis/qdrant service containers, so `/ready` is asserted to report each store `ok` — a stubbed probe would fail it. Adds chat + SSE assertions |
| **Visual architecture doc** | `architecture.html` at the repo root — generated from `docs/architecture.md` with rendered Mermaid diagrams (component map, liveness-vs-readiness flow, chat/SSE sequence, datastore lifecycle). Regenerated every stage; a test fails on drift (§ 7) |
| **Quality gate** | ruff, mypy strict, **60 tests** (was 21) |

## 3. Files created or modified

**Created**

```
shared/datastores.py                        connection layer + registry
services/api/schemas.py                     chat request/response/chunk schemas
services/api/completions.py                 CompletionEngine protocol + EchoEngine
services/api/routes/chat.py                 POST /v1/chat/completions (+ SSE)
tests/fakes.py                              FakeDatastore, shared by two test modules
tests/unit/test_datastores.py               13 tests
tests/unit/test_chat.py                     15 tests
tests/{__init__,unit/__init__}.py           make `tests` a real package (see § 7)
docs/adr/0004-streaming-transport.md
docs/adr/0005-datastore-connection-pooling.md
scripts/build_architecture.py               architecture.md -> architecture.html
architecture.html                           GENERATED — do not edit
tests/unit/test_architecture.py             4 tests; fails if the HTML drifts
```

**Modified (docs)**

```
docs/architecture.md        was still entirely Stage 1 — see § 7
CLAUDE.md                   regeneration convention; quirks merged
```

**Modified**

```
shared/config.py            pooling settings; prod URL validator; test profile ignores root .env
services/api/app.py         registry + engine on app.state; lifespan startup/shutdown; /v1 router
services/api/routes/health.py   get_datastores dependency; real probes; 503 path
tests/unit/test_config.py   prod test now supplies URLs (contract changed — see § 7)
tests/unit/test_health.py   Stage-1 `checks == {}` assertion replaced by real probe tests
.github/workflows/ci.yml    datastore service containers; /ready, chat, SSE assertions
docker-compose.yml          Stage-1 "does NOT connect" comments corrected
.env.example                datastore URLs uncommented; pooling knobs documented
pyproject.toml              mypy override for asyncpg (no py.typed); uv link-mode (§ 6)
docs/adr/README.md          index 0004, 0005
```

### `link-mode = "copy"` pinned in `pyproject.toml`

uv's default hardlink install mode fails across drives — and fails **silently**,
leaving a package in `uv.lock` but absent from the venv, which surfaces much
later as a baffling `ModuleNotFoundError`. This repo is on `D:` while uv's cache
defaults to `C:`, and it has bitten twice on this machine.

It was previously papered over by a `UV_LINK_MODE=copy` User env var and exports
in `scripts/verify.*`. Neither travels with the repo, so a fresh clone onto a
drive other than the cache would hit it again. Declaring it under `[tool.uv]`
fixes it for everyone; the env var still overrides for anyone who needs it to.

Verified by feeding uv an invalid value, which proves the key is genuinely parsed
rather than silently ignored:

```console
$ uv sync --frozen        # with link-mode = "definitely-not-a-mode"
warning: Failed to parse `pyproject.toml` during settings discovery:
  TOML parse error at line 86, column 13
  unknown variant `definitely-not-a-mode`,
  expected one of `clone`, `reflink`, `copy`, `hardlink`, `symlink`
```

and by forcing a real cross-drive reinstall with the env var unset:

```console
$ env -u UV_LINK_MODE uv sync --frozen --reinstall-package asyncpg
Installed 1 package in 199ms
 ~ asyncpg==0.31.0
$ env -u UV_LINK_MODE uv run python -c "import asyncpg; print(asyncpg.__version__)"
0.31.0
```

## 4. Architecture decisions

| Decision | Where | Short rationale |
|---|---|---|
| SSE over WebSockets / chunked JSON / gRPC | ADR 0004 | Generation is one-directional; plain HTTP survives ingress and header auth; mirrors the format Stage 3's backends already emit |
| OpenAI-shaped wire format | ADR 0004 | Existing SDKs work unchanged; Stage 3's seam stays a pass-through, not a translation layer |
| Pool per store, opened in the lifespan | ADR 0005 | Handshake once, not per request; native driver pools rather than hand-rolled |
| Qdrant via pooled `httpx`, not `qdrant-client` | ADR 0005 | `qdrant-client` is a Stage 4 dependency; a readiness probe does not justify pulling it into base and undercutting the stub rule. **Consequence: `httpx` is now a runtime dep** |
| Startup never crashes; connects concurrently | ADR 0005 | An unreachable store must yield a diagnosable un-ready pod, not a crash loop — and sequential timeouts delayed liveness ~20s, long enough to trip a liveness probe and cause that crash loop anyway (§ 5) |
| `not_configured` passes readiness; `prod` requires URLs at boot | ADR 0005 | Keeps the test profile hermetic while closing the silent-pass hole at the config boundary — a misconfigured service should never start, not start and quietly report itself un-ready |

## 5. Validation completed — real output

### Quality gate

```console
$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
41 files already formatted

$ uv run mypy
Success: no issues found in 40 source files

$ uv run pytest
56 passed, 1 warning in 0.60s
```

### Compose stack

```console
$ docker compose up -d --build
 Image production-llm-platform/api:0.1.0  Built
 Container production-llm-platform-api-1  Started

$ docker compose ps --format "table {{.Service}}\t{{.Status}}"
SERVICE      STATUS
api          Up 11 seconds (healthy)
grafana      Up 6 minutes
postgres     Up 7 minutes (healthy)
prometheus   Up 6 minutes
qdrant       Up 7 minutes
redis        Up 5 minutes (healthy)
```

### `/health` and `/ready` against live datastores

```console
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
x-request-id: f917b19a88654f85a13ffa22f380cb9e
{"status":"ok","service":"api","version":"0.1.0","environment":"dev"}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"status":"ready","checks":{"postgres":"ok","redis":"ok","qdrant":"ok"}}
```

### The probes are real — proven by killing a dependency

A 200 proves nothing on its own; a stub would also return one. Stopping Redis:

```console
$ docker compose stop redis
$ curl -i http://localhost:8000/ready
HTTP/1.1 503 Service Unavailable
{"status":"not_ready","checks":{"postgres":"ok","redis":"unavailable","qdrant":"ok"}}

$ curl -o /dev/null -w "%{http_code}" http://localhost:8000/health
200                       # liveness does NOT follow a dependency

$ docker compose start redis
$ curl -s http://localhost:8000/ready
{"status":"ready","checks":{"postgres":"ok","redis":"ok","qdrant":"ok"}}   # recovers
```

### Chat completion — real curl, not a unit test

```console
$ curl -X POST http://localhost:8000/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"messages":[{"role":"user","content":"hello from curl"}]}'
{"id":"chatcmpl-3ddef37522d64287aa208ca7a95fbe63","object":"chat.completion",
 "created":1784042410,"model":"mock-echo","choices":[{"index":0,
 "message":{"role":"assistant","content":"You said: hello from curl"},
 "finish_reason":"stop"}],
 "usage":{"prompt_tokens":3,"completion_tokens":5,"total_tokens":8}}
```

### SSE streaming — real curl

```console
$ curl -N -i -X POST http://localhost:8000/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"messages":[{"role":"user","content":"streaming over the wire"}],"stream":true}'
HTTP/1.1 200 OK
cache-control: no-cache
x-accel-buffering: no
content-type: text/event-stream; charset=utf-8
transfer-encoding: chunked

data: {"id":"chatcmpl-ff83…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":"You"}}]}

data: {"id":"chatcmpl-ff83…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" said:"}}]}

data: {"id":"chatcmpl-ff83…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" streaming"}}]}

…

data: {"id":"chatcmpl-ff83…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### Validation errors use the uniform envelope

```console
$ curl -X POST http://localhost:8000/v1/chat/completions \
    -H 'Content-Type: application/json' -d '{"messages":[]}'
HTTP/1.1 422 Unprocessable Entity
{"error":{"type":"validation_error","message":"[{'type': 'too_short', …}]",
 "request_id":"1e601e03a7b040f586987242c0d5f4ae"}}
```

### Fail-loud config, tested against the real image

```console
$ docker run --rm -e ENVIRONMENT=prod production-llm-platform/api:0.1.0
Traceback (most recent call last):
  …
  Value error, the prod profile requires these datastore URLs to be set:
  DATABASE_URL, REDIS_URL, QDRANT_URL
$ echo $?
1
```

And with URLs pointing at an unroutable host, the container **boots anyway** and
reports the truth rather than crash-looping:

```console
$ docker run -d -e ENVIRONMENT=prod \
    -e DATABASE_URL=postgresql://u:p@10.255.255.1:5432/db \
    -e REDIS_URL=redis://10.255.255.1:6379/0 \
    -e QDRANT_URL=http://10.255.255.1:6333 production-llm-platform/api:0.1.0
$ curl -s http://localhost:8099/ready
{"status":"not_ready","checks":{"postgres":"unavailable","redis":"unavailable","qdrant":"unavailable"}}
```

**This scenario is what caught the sequential-startup bug.** `/health` first
served after **~20s** because each connect burned its own 5s timeout in turn —
long enough for a Kubernetes liveness probe to kill the pod, which is exactly the
crash loop the never-raise design exists to prevent. After making `startup()`
concurrent: **~7s**, same correct `/ready` output. A regression test pins it.

### Secret scan

```console
$ grep -rnE "(password|secret|token|api[_-]?key)\s*[:=]\s*['\"][^'\"]+['\"]" shared/ services/
  none found
$ grep -rnE "(postgresql|redis|http)://[^\"' ]*:[^\"' ]*@" shared/ services/
  none found
$ git check-ignore -v .env
.gitignore:25:.env	.env
```

## 6. Known limitations

- **No LLM.** `EchoEngine` returns `"You said: <last user message>"`. Token
  counts are whitespace splits, not a tokenizer. Stage 3 replaces both.
- **No reconnect after a failed boot.** A store whose `connect` raised stays
  `unavailable` until the process restarts. A store that connected and later
  blipped *does* recover (verified with Redis above) because the driver pools
  reconnect on the next ping.
- **Pool sizes are guesses.** Defaults of 10 are untuned; Stage 9's load testing
  should set them.
- **Nothing is persisted.** Postgres and Redis are connected and probed but no
  schema, migration, cache read or write exists yet. Qdrant is only `GET /readyz`.
- **`prod` refuses to boot without `QDRANT_URL`**, which no code queries until
  Stage 4. Deliberate — consistent beats a special case that erodes.
- **Streaming bypasses `response_model`**, so FastAPI does not enforce the
  streamed shape; the tests carry that weight.
- **No cancellation.** SSE is one-directional; a client that disconnects
  mid-stream is not detected. Fine for a mock, revisit when generation is real.
- `/v1` prefix is applied, but **no pagination conventions** — nothing to
  paginate yet (`PROJECT_STATUS.md` listed this under Stage 2's expected scope;
  the stage prompt's § 4 did not).
- **API remains unauthenticated** — Stage 8.

## 7. Technical debt

- **`docs/architecture.md` was missed during the stage and caught afterwards.**
  It still described Stage 1 in full — `/ready` as `checks: {}`, datastores as
  "NOT connected to the app yet", and chat/streaming/datastore wiring under
  "Planned". `CLAUDE.md` and `PROJECT_STATUS.md` were updated; this one was not,
  and nothing flagged it. Now rewritten for Stage 2, and it is the source for the
  new `architecture.html`. **The real lesson:** three docs described the same
  state and only a human noticing caught the third. The drift test now covers
  md→html, but nothing yet enforces that `architecture.md` matches reality — that
  still relies on the stage checklist.
- **`architecture.html` is generated, and Mermaid loads from a pinned CDN.**
  Offline, the prose renders but diagrams stay as text. Inlining ~3MB of vendored
  JS into every diff was judged the worse trade.
- **`tests/` became a package.** `tests/__init__.py` and `tests/unit/__init__.py`
  were added because mypy saw `tests/fakes.py` under two module names
  (`fakes` and `tests.fakes`) via PEP 420 namespace resolution. This is mypy's
  own recommended fix, but it is a structural change Stage 1 did not have.
- **Two Stage-1 test contracts were deliberately changed**, not accommodated:
  - `test_readiness_returns_200_with_empty_checks` — the `checks == {}`
    assertion was a Stage-1 contract this stage invalidates by design.
  - `test_prod_profile_switch` — previously loaded the prod profile with no
    datastore URLs, which the new validator defines as invalid. It now supplies
    URLs, and a new test pins the fail-loud behaviour.
- **`docs/verification-log/stage-02-fastapi-service.md`** is untracked, empty,
  and still carries the old `fastapi-service` name while the prompt and this
  summary use `api`. **Left alone — that file is the maintainer's**, per the
  stage prompt § 6.
- `config/environments/*.env` can still drift from `.env.example`; nothing
  enforces it (inherited from Stage 1).
- The `422` envelope stringifies Pydantic's raw error list into `message` — ugly
  and it leaks internal field paths. Worth a structured `details` field.

## 8. Items intentionally deferred

| Stage | Deferred |
|:--:|---|
| 3 | Real model calls; orchestrator-backed `CompletionEngine`; agents |
| 4 | Qdrant vector operations via `qdrant-client`; RAG |
| 5 | OpenTelemetry export; Grafana dashboards |
| 6 | Evaluation, MLOps pipelines |
| 7 | Kubernetes manifests (readiness → `/ready`, liveness → `/health`), Terraform |
| 8 | Auth, guardrails, rate limiting |
| 9 | Load/chaos testing; pool tuning; reconnect/circuit breaking |

Also deferred: DB schema/migrations, Redis caching, pagination, API keys.

## 9. Prerequisites for Stage 3

All met:

- `CompletionEngine` protocol + `get_engine` dependency — swap `EchoEngine` for
  an orchestrator-backed engine with no route change.
- SSE plumbing proven end-to-end against a mock, so a streaming bug in Stage 3
  is a model-integration bug, not a transport bug.
- `services/orchestrator` is still an untouched stub raising `NotImplementedError`.
- Postgres and Redis are pooled and reachable for conversation state / caching.
- `langchain==1.3.13` and `langgraph==1.2.9` remain pinned in the
  `orchestration` extra, ready to promote.
