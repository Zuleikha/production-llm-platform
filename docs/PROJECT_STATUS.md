# Project status

**Canonical source of truth for project progress and the stage roadmap.**
Every stage prompt references this file. Update it at the end of each stage.

| | |
|---|---|
| **Current version** | `0.1.0` |
| **Current stage** | Stage 4 — RAG (**complete**) |
| **Overall progress** | **4 / 10 stages — 40%** |
| **Next milestone** | Stage 5 — Observability |
| **Last updated** | 2026-07-16 |

```
Progress  [████──────]  4/10
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
| 4 | RAG | [stage-04-rag.md](stage-summaries/stage-04-rag.md) | [stage-04-rag.md](verification-log/stage-04-rag.md) | 2026-07-16 |

## Remaining stages

| Stage | Name | Summary file (fixed) | Objective |
|:-----:|------|----------------------|-----------|
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
  deterministic offline tools: `calculator`, `text_stats`, `json_query`, plus a
  `document_search` retrieval tool wired in when Qdrant is available (Stage 4).
- **Grounded answers with citations (Stage 4)** — a document corpus is ingested
  (LlamaIndex chunking → Voyage embeddings → Qdrant) by `scripts/ingest.py`; the
  agent retrieves relevant passages via `document_search` and answers from them.
  Retrieved chunks surface as a new top-level `citations` field on the response
  (ADR 0013). Retrieved text is fenced as untrusted data behind a per-call nonce
  (ADR 0014). Qdrant now holds real data.
- **Second hermetic provider seam (Stage 4)** — the `test` profile *cannot*
  construct a real Voyage embeddings client either; embeddings go through a
  deterministic offline hash. CI needs no `VOYAGE_API_KEY` (ADR 0011).
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
  and Qdrant (`qdrant-client`, probed via `get_collections()`); opened
  concurrently in the lifespan, closed on shutdown (ADR 0005, 0012).
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
  **`prod` refuses to boot** without all three datastore URLs, its
  `ANTHROPIC_API_KEY`, *or its `VOYAGE_API_KEY`* (ADR 0005, 0006, 0011); the
  `test` profile ignores the root `.env` so the suite stays hermetic.
- **Prometheus metrics** — request counter + latency histogram, labelled by
  route template; scraped successfully by the Prometheus container.
- **Global error handling** — uniform `{"error": {...}}` envelope; 500s never
  leak internals.
- **Lifespan hooks** — `service.startup` / `service.shutdown`.
- **Docker Compose stack** — api (multi-stage, **non-root** uid 1001), postgres,
  redis, qdrant, prometheus, grafana (datasource provisioned as code).
- **Quality gate** — ruff, ruff-format, mypy `strict`, pytest (246 tests, plus
  opt-in live-datastore / live-Qdrant / live-provider layers that skip by
  default), pre-commit; GitHub Actions runs all of it plus a Docker build against
  live postgres/redis/qdrant service containers, asserting `/ready` reports every
  store `ok` and that chat + SSE work.

### ❌ Does not exist yet

**No authentication** (the corpus and API are both unauthenticated) · no
OpenTelemetry backend · no Grafana dashboards · no Kubernetes/Terraform · no
evaluation · no load testing.

All three datastores now hold real data — Postgres (conversation history), Redis
(its cache), and **Qdrant (document vectors, new in Stage 4)**.

Retrieval is deliberately minimal: no reranking, no hybrid search, no query
expansion, no automatic re-ingestion (an operator runs `scripts/ingest.py`), and
editing a document shorter leaves orphaned tail chunks (ADR 0012). The
prompt-injection mitigation is delimiting + labelling, not full hardening — no
classifier, no trust tiers, no egress filtering (ADR 0014, Stage 8).

Within the agent stack, deliberately deferred: prompt caching, context
compaction (a long enough conversation will exceed the context window and fail),
per-conversation concurrency control, and retry / circuit breaking around the
Anthropic call beyond the SDK's defaults (Stage 9).

API versioning (`/v1`) is in place, but **pagination conventions are deferred** —
no endpoint returns a collection yet. Revisit when one does.

---

## Next milestone — Stage 5 (Observability)

**Objective:** real distributed tracing and metrics export, dashboards, alerting.

Expected scope:

1. `services/monitoring` — a `SpanExporter`; make `@traced` emit real
   OpenTelemetry spans without touching call sites (the seam exists for this).
2. OTel traces + metrics export to a collector; wire the pinned `observability`
   extra.
3. Grafana dashboards provisioned as code; alerting rules on symptom-level
   signals.

**Prerequisites** (all met): the `@traced` seam on every application function,
structured JSON logging, Prometheus metrics + Grafana already in the compose
stack.

**Resolved in Stage 4 (was carried forward from Stage 3):** the missing contract
test against the real external APIs. Stage 4 added an opt-in, double-gated live
contract test (ADR 0015) that makes one real call each to the Anthropic chat API
and the Voyage embeddings API and asserts the response shapes the code assumes.
It is not run in CI (CI stays key-free and hermetic) — a human runs it
deliberately when changing `llm.py` or `embeddings.py`.

**Note carried to Stage 8 (security):** the retrieval prompt-injection mitigation
shipped in Stage 4 is delimiting + labelling only (ADR 0014). It removes
ambiguity about what is data but is not immunity, and there is no classifier,
trust tiering, or answer egress filtering. Widening what can enter the corpus
(user uploads, crawls, third-party feeds) changes the threat model and must
revisit ADR 0014.
