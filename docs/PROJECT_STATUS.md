# Project status

**Canonical source of truth for project progress and the stage roadmap.**
Every stage prompt references this file. Update it at the end of each stage.

| | |
|---|---|
| **Current version** | `0.1.0` |
| **Current stage** | Stage 2 — API (**complete**) |
| **Overall progress** | **2 / 10 stages — 20%** |
| **Next milestone** | Stage 3 — Agents |
| **Last updated** | 2026-07-14 |

```
Progress  [██────────]  2/10
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

## Completed stages

| Stage | Name | Summary | Verification | Completed |
|:-----:|------|---------|--------------|-----------|
| 1 | Foundation | [stage-01-foundation.md](stage-summaries/stage-01-foundation.md) | [stage-01-foundation.md](verification-log/stage-01-foundation.md) | 2026-07-14 |
| 2 | API | [stage-02-api.md](stage-summaries/stage-02-api.md) | [stage-02-api.md](verification-log/stage-02-api.md) | 2026-07-14 |

## Remaining stages

| Stage | Name | Summary file (fixed) | Objective |
|:-----:|------|----------------------|-----------|
| 3 | Agents | `stage-03-agents.md` | Agent loop + tool use (LangChain/LangGraph); implement `Agent` and `Orchestrator` |
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
  a deterministic **echo engine** behind a `CompletionEngine` protocol. **No LLM
  is called** — Stage 3 swaps the engine in without touching the routes.
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
  **`prod` refuses to boot** without all three datastore URLs (ADR 0005); the
  `test` profile ignores the root `.env` so the suite stays hermetic.
- **Prometheus metrics** — request counter + latency histogram, labelled by
  route template; scraped successfully by the Prometheus container.
- **Global error handling** — uniform `{"error": {...}}` envelope; 500s never
  leak internals.
- **Lifespan hooks** — `service.startup` / `service.shutdown`.
- **Docker Compose stack** — api (multi-stage, **non-root** uid 1001), postgres,
  redis, qdrant, prometheus, grafana (datasource provisioned as code).
- **Quality gate** — ruff, ruff-format, mypy `strict`, pytest (56 tests),
  pre-commit; GitHub Actions runs all of it plus a Docker build against live
  postgres/redis/qdrant service containers, asserting `/ready` reports every
  store `ok` and that chat + SSE work.

### ❌ Does not exist yet

No LLM calls · no agents · no RAG/vector search · **no authentication** · no
OpenTelemetry backend · no Grafana dashboards · no Kubernetes/Terraform · no
evaluation · no load testing.

Postgres, Redis and Qdrant are now **connected and probed**, but nothing is
persisted: no schema, migrations, cache reads/writes, or vector operations.
Qdrant is only reached for `GET /readyz`.

API versioning (`/v1`) is in place, but **pagination conventions are deferred** —
Stage 2 added no listable resources, so there is nothing to paginate. Revisit
when an endpoint returns a collection.

---

## Next milestone — Stage 3 (Agents)

**Objective:** replace the mock engine with a real agent loop.

Expected scope:

1. Implement `Agent` and `Orchestrator` (currently stubs raising `NotImplementedError`).
2. LangGraph-based orchestration; promote the `orchestration` extra to base deps.
3. An orchestrator-backed `CompletionEngine` swapped in behind the existing
   protocol — the routes and SSE plumbing should not change.
4. Real model calls, real token accounting, tool use.
5. Conversation state in Postgres / caching in Redis.

**Prerequisites** (all met): the `CompletionEngine` seam + `get_engine`
dependency, SSE plumbing proven against a mock, pooled datastores, CI gate green.
See [stage-02-api.md § Prerequisites for Stage 3](stage-summaries/stage-02-api.md#9-prerequisites-for-stage-3).
