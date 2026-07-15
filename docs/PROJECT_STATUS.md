# Project status

**Canonical source of truth for project progress and the stage roadmap.**
Every stage prompt references this file. Update it at the end of each stage.

| | |
|---|---|
| **Current version** | `0.1.0` |
| **Current stage** | Stage 3 — Agents (**complete**) |
| **Overall progress** | **3 / 10 stages — 30%** |
| **Next milestone** | Stage 4 — RAG |
| **Last updated** | 2026-07-15 |

```
Progress  [███───────]  3/10
```

---

## Stage summary naming convention (fixed — do not drift)

Every stage ends with a summary at exactly this path and filename. This list is
canonical; future stage prompts must use these names verbatim.

```
docs/stage-summaries/stage-01-foundation.md
docs/stage-summaries/stage-02-api.md
docs/stage-summaries/stage-03-agents.md
docs/stage-summaries/stage-04-rag.md
docs/stage-summaries/stage-05-observability.md
docs/stage-summaries/stage-06-mlops.md
docs/stage-summaries/stage-07-kubernetes.md
docs/stage-summaries/stage-08-security.md
docs/stage-summaries/stage-09-reliability.md
docs/stage-summaries/stage-10-portfolio.md

```

## Verification log naming convention (fixed — do not drift)

Every stage's independent manual verification (separate from CC's self-report)
is logged at exactly this path and filename. This list is canonical; naming
must match the stage-summaries convention above exactly.

```
docs/verification-log/stage-01-foundation.md
docs/verification-log/stage-02-api.md
docs/verification-log/stage-03-agents.md
docs/verification-log/stage-04-rag.md
docs/verification-log/stage-05-observability.md
docs/verification-log/stage-06-mlops.md
docs/verification-log/stage-07-kubernetes.md
docs/verification-log/stage-08-security.md
docs/verification-log/stage-09-reliability.md
docs/verification-log/stage-10-portfolio.md
```

## Architecture doc (visual, regenerated every stage)

`docs/architecture.md` is the source of truth. **`architecture.html`** at the
repo root is generated from it — component map, liveness vs readiness, the agent
loop, the chat/SSE sequence, conversation caching, datastore lifecycle. Open it
in a browser for the visual view.

Diagrams are **pre-rendered to inline SVG** under `docs/diagrams/` (ADR 0010):
the page loads no CDN, runs no JavaScript, and renders offline. Regenerating a
diagram needs Node + `npx`; **building the page and running `--check` need only
Python**, so CI and the test suite carry no JS toolchain.

```
uv run python scripts/build_architecture.py           # regenerate
uv run python scripts/build_architecture.py --check   # CI/test: fail on drift
```

**Every stage updates `docs/architecture.md` and regenerates.** Never edit the
HTML — `tests/unit/test_architecture.py` fails when the two disagree.

## Completed stages

| Stage | Name | Summary | Verification | Completed |
|:-----:|------|---------|--------------|-----------|
| 1 | Foundation | [stage-01-foundation.md](stage-summaries/stage-01-foundation.md) | [stage-01-foundation.md](verification-log/stage-01-foundation.md) | 2026-07-14 |
| 2 | API | [stage-02-api.md](stage-summaries/stage-02-api.md) | [stage-02-api.md](verification-log/stage-02-api.md) | 2026-07-14 |
| 3 | Agents | [stage-03-agents.md](stage-summaries/stage-03-agents.md) | [stage-03-agents.md](verification-log/stage-03-agents.md) | 2026-07-15 |

## Remaining stages

| Stage | Name | Summary file (fixed) | Objective |
|:-----:|------|----------------------|-----------|
| 4 | RAG | `stage-04-rag.md` | Retrieval: LlamaIndex ingestion/chunking, embeddings, Qdrant-backed `Retriever`/`VectorStore`, grounded answers with citations |
| 5 | Observability | `stage-05-observability.md` | OpenTelemetry traces/metrics export, `@traced` → real spans, Grafana dashboards, alerting |
| 6 | MLOps | `stage-06-mlops.md` | Evaluation (`Evaluator`), eval datasets, LLM-as-judge, regression gates in CI, pipelines |
| 7 | Kubernetes | `stage-07-kubernetes.md` | K8s manifests/Helm (probes → `/health`, `/ready`), Terraform provisioning |
| 8 | Security | `stage-08-security.md` | AuthN/AuthZ, input/output guardrails, rate limiting, secret management, security scanning |
| 9 | Reliability | `stage-09-reliability.md` | Load testing, chaos/failure testing, SLOs, resilience patterns |
| 10 | Portfolio | `stage-10-portfolio.md` | Final polish, docs, demos, case study writeup |

---

## Current repository capabilities

### ✅ Works today

- **`api` service (FastAPI)** — `GET /health`, `/ready`, `/version`, `/metrics`,
  `POST /v1/chat/completions`, plus generated OpenAPI docs at `/docs`.
- **Chat completion endpoint** — OpenAI-shaped schemas and validation, backed by
  a **real LangGraph agent loop calling the Anthropic API** (`claude-opus-4-8`)
  behind the `CompletionEngine` protocol. Stage 2's echo engine is gone; the
  routes and wire format did not change, which is what the seam was for.
- **Agent loop with tool use** — `reason → select tool → execute → observe →
  answer`, as an explicit LangGraph state machine, bounded by a step cap. Three
  deterministic offline tools: `calculator`, `text_stats`, `json_query`.
- **Real token accounting** — the model's own `input_tokens` / `output_tokens`,
  summed across every model call a run makes (ADR 0006).
- **Conversation state** — Postgres schema + a forward-only SQL migration runner
  applied on boot (ADR 0007), behind a Redis read-through cache where Postgres
  stays the source of truth (ADR 0008). Opt-in per request via `conversation_id`;
  without one the endpoint is stateless exactly as in Stage 2.
- **Hermetic by construction** — the `test` profile *cannot* construct a real
  Anthropic client, so no test can spend money regardless of what is in the
  environment. CI needs no `ANTHROPIC_API_KEY` (ADR 0009).
- **SSE streaming** — `"stream": true` returns `text/event-stream` with
  `data: {json}` frames terminated by `data: [DONE]` (ADR 0004).
- **Pooled datastore connections** — Postgres (asyncpg), Redis (redis.asyncio)
  and Qdrant (pooled `httpx` → `/readyz`); opened concurrently in the lifespan,
  closed on shutdown (ADR 0005).
- **Real readiness probes** — `/ready` concurrently probes every configured store
  and returns **503** `not_ready` when any is unavailable; `/health` stays pure
  liveness and touches no datastore.
- **Structured JSON logging** — one object per line with `timestamp`, `level`,
  `service`, `environment`, `request_id`, `logger`, `message`, and an
  `exception` trace on errors.
- **Request-id correlation** — `X-Request-ID` honoured inbound or generated,
  bound to a `ContextVar`, echoed on the response, present on every access log.
- **Typed layered configuration** — pydantic-settings with `dev`/`test`/`prod`
  profiles; OS env > `.env` > profile file > defaults; no secrets committed.
  **`prod` refuses to boot** without all three datastore URLs *or its
  `ANTHROPIC_API_KEY`* (ADR 0005, 0006); the `test` profile ignores the root
  `.env` so the suite stays hermetic.
- **Prometheus metrics** — request counter + latency histogram, labelled by
  route template; scraped successfully by the Prometheus container.
- **Global error handling** — uniform `{"error": {...}}` envelope; 500s never
  leak internals.
- **Lifespan hooks** — `service.startup` / `service.shutdown`.
- **Docker Compose stack** — api (multi-stage, **non-root** uid 1001), postgres,
  redis, qdrant, prometheus, grafana (datasource provisioned as code).
- **Quality gate** — ruff, ruff-format, mypy `strict`, pytest (185 tests),
  pre-commit; GitHub Actions runs all of it plus a Docker build against live
  postgres/redis/qdrant service containers, asserting `/ready` reports every
  store `ok` and that chat + SSE work.

### ❌ Does not exist yet

No RAG/vector search · **no authentication** · no OpenTelemetry backend · no
Grafana dashboards · no Kubernetes/Terraform · no evaluation · no load testing.

**Qdrant is still connected and probed but holds no data** — it is reached only
for `GET /readyz`, and belongs to Stage 4. Postgres and Redis now hold real data
(conversation history and its cache).

Within the agent stack, deliberately deferred: prompt caching, context
compaction (a long enough conversation will exceed the context window and fail),
per-conversation concurrency control, and retry / circuit breaking around the
Anthropic call beyond the SDK's defaults (Stage 9).

API versioning (`/v1`) is in place, but **pagination conventions are deferred** —
no endpoint returns a collection yet. Revisit when one does.

---

## Next milestone — Stage 4 (RAG)

**Objective:** grounded answers with citations.

Expected scope:

1. Implement `Retriever` / `VectorStore` (currently stubs raising `NotImplementedError`).
2. LlamaIndex ingestion + chunking; embeddings; promote the `retrieval` extra.
3. Qdrant-backed vector search — swap the Stage 2 `httpx` readiness probe for
   `qdrant-client` behind the same `Datastore` contract.
4. A retrieval tool in the agent's registry, so the loop can ground its answers.
5. Citations in the response.

**Prerequisites** (all met): the agent loop and its `ToolRegistry` (a retrieval
tool is a new `Tool`, not a new loop), the `Datastore` seam, Qdrant already
pooled and probed, and conversation persistence.

**Note for Stage 4:** Stage 3's tools are pure functions of their arguments,
which is what makes tool results safe to feed back into the model unexamined.
A retrieval tool returns *document text* — potentially attacker-controlled — so
prompt injection through tool results becomes a real concern the moment it lands.
