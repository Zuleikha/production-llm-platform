# Project status

**Canonical source of truth for project progress and the stage roadmap.**
Every stage prompt references this file. Update it at the end of each stage.

| | |
|---|---|
| **Current version** | `0.1.0` |
| **Current stage** | Stage 9 — Reliability (**complete**) |
| **Overall progress** | **9 / 10 stages — 90%** |
| **Next milestone** | Stage 10 — Portfolio |
| **Last updated** | 2026-07-24 |

```
Progress  [█████████─]  9/10
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
| 5 | Observability | [stage-05-observability.md](stage-summaries/stage-05-observability.md) | [stage-05-observability.md](verification-log/stage-05-observability.md) | 2026-07-18 |
| 6 | MLOps | [stage-06-mlops.md](stage-summaries/stage-06-mlops.md) | [stage-06-mlops.md](verification-log/stage-06-mlops.md) | 2026-07-20 |
| 7 | Kubernetes | [stage-07-kubernetes.md](stage-summaries/stage-07-kubernetes.md) | [stage-07-kubernetes.md](verification-log/stage-07-kubernetes.md) | 2026-07-21 |
| 8 | Security | [stage-08-security.md](stage-summaries/stage-08-security.md) | [stage-08-security.md](verification-log/stage-08-security.md) | 2026-07-23 |
| 9 | Reliability | [stage-09-reliability.md](stage-summaries/stage-09-reliability.md) | [stage-09-reliability.md](verification-log/stage-09-reliability.md) | 2026-07-24 |

## Remaining stages

| Stage | Name | Summary file (fixed) | Objective |
|:-----:|------|----------------------|-----------|
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
- **Distributed tracing (Stage 5)** — `@traced` (on ~30 functions since Stage 1)
  now emits a real **OpenTelemetry span** as well as its DEBUG log, and each HTTP
  request is a root span via FastAPI auto-instrumentation. Spans export over
  OTLP/HTTP to an **OTel Collector** and land in **Grafana Tempo**, read as a
  native Grafana datasource. Hermetic by construction — the `test` profile
  *cannot* construct the exporting provider (ADR 0016), so the suite makes no
  network call and needs no collector. Spans carry static code identity only
  (`code.function`, `code.namespace`) — never arguments, return values, or the
  exception message as a searchable status.
- **Grafana dashboards + alerting as code (Stage 5)** — one provisioned
  dashboard (`api-overview`: RED metrics from Prometheus + Tempo trace tables) and
  three symptom-level alert rules (error rate > 5%, chat p99 > 30s, `/ready` 503)
  via Grafana-native unified alerting — no Alertmanager container.
- **Global error handling** — uniform `{"error": {...}}` envelope; 500s never
  leak internals.
- **Lifespan hooks** — `service.startup` / `service.shutdown`.
- **Docker Compose stack** — api (multi-stage, **non-root** uid 1001), postgres,
  redis, qdrant, prometheus, grafana (datasources provisioned as code), plus
  **otel-collector** and **tempo** (traces, Stage 5).
- **RAG evaluation + CI regression gate (Stage 6)** — `services/evaluation` scores
  the retrieval pipeline against a checked-in dataset (`data/eval/`) via an
  operator/CI script (`scripts/evaluate.py`, never a boot hook). **Tier 1**
  (deterministic recall@k / MRR over the offline-hash embeddings and an in-memory
  cosine store) is hermetic, key-free, and a **required CI job** that fails the
  build when a score drops below the checked-in baseline minus tolerance. **Tier 2**
  (LLM-as-judge: faithfulness + citation accuracy) is opt-in, billable, and never
  in CI — same double opt-in as the live contract test (ADR 0015, 0017).
- **Kubernetes deployment via Helm (Stage 7)** — the `api` image deploys to a
  Kubernetes cluster through a Helm chart
  (`infrastructure/kubernetes/helm/production-llm-platform/`) with
  `livenessProbe → /health` and `readinessProbe → /ready`, a ConfigMap for
  non-secret settings and a Secret for URLs/keys injected at install, resource
  requests/limits, configurable replicas, and an optional HPA. Verified end-to-end
  on a real local `kind` cluster: every pod reaches `Ready` and `/ready` reports
  all three datastores `ok`. Minimal **dev-only** in-cluster Postgres/Redis/Qdrant
  (gated by `devDependencies.enabled`) make that local verification possible;
  production points the datastore URLs at Terraform-managed services (ADR 0018).
- **Cloud provisioning as code (Stage 7)** — Terraform modules
  (`infrastructure/terraform/`, target AWS) for networking (VPC), cluster (EKS),
  Postgres (RDS), Redis (ElastiCache), object storage (S3) and secrets (Secrets
  Manager references, no values). **Validated, never applied**: `terraform init`
  (local backend) + `validate` + `fmt -check` pass and are the CI-safe gate;
  `plan`/`apply` need real AWS credentials this project does not have and are a
  deliberate scope boundary (ADR 0018).
- **Quality gate** — ruff, ruff-format, mypy `strict`, pytest (**362 tests**, up
  from the Stage 7 baseline of 311, plus opt-in live-datastore / live-Qdrant /
  live-provider layers that skip by default), pre-commit; GitHub Actions runs all
  of it plus the hermetic Tier 1 eval regression gate and a Docker build against
  live postgres/redis/qdrant service containers, asserting `/ready` reports every
  store `ok` and that authenticated chat + SSE work (and that a missing key `401`s).
  **Stage 7 added three hermetic infra jobs** (`helm lint`/`template`, `terraform
  fmt`/`validate`, and a `kind` deploy). **Stage 8 adds two hermetic security jobs**:
  a **gitleaks** secret scan and a **pip-audit** dependency-vulnerability scan, both
  pinned (ADR 0019).
- **API authentication + rate limiting (Stage 8)** — `POST /v1/chat/completions`
  requires `Authorization: Bearer <key>`; only a salted `HMAC-SHA256(pepper, key)`
  is stored, compared constant-time, and missing/malformed/wrong keys all return
  the **same** `401` in the uniform envelope. A Redis-backed, per-principal rate
  limiter (atomic Lua `INCR`+`EXPIRE`) returns `429` over the threshold and
  **fails open + logs** on a Redis outage (ADR 0008/0019). `/health`, `/ready`,
  `/version`, `/metrics` stay unauthenticated by design (probe/scrape paths). The
  `prod` profile now also refuses to boot without `API_KEYS` + `API_KEY_HASH_SECRET`.
- **RAG-hardening guardrails on top of the ADR 0014 nonce fence (Stage 8)** — a
  heuristic **input** guardrail screens the user turn (blocks direct
  instruction-override / system-prompt-extraction → `400`, logs persona/PII only);
  an **excerpt** guardrail flags injection-shaped retrieved text (log-only, never
  drops); an **answer-egress** check flags leaked fence markers or echoed preamble
  (log-only). The nonce fence stays the load-bearing mitigation (ADR 0019).
- **Two hermetic security CI gates (Stage 8)** — **gitleaks** (`v8.30.1`, pinned)
  scans the tree against a `.gitleaks.toml` that allowlists only documented fake
  test fixtures, and **pip-audit** (`==2.9.0`, pinned) audits the exported
  `uv.lock`. Both are free, key-free, and fail the build on a finding (ADR 0019).

### ❌ Does not exist yet

Evaluation covers **RAG retrieval only** (ADR 0017) — agent tool-use and
open-ended chat quality are out of scope, having no fixture corpus to grade
against. **Per-conversation concurrency control** is still absent (ADR 0008's
known gap, out of Stage 9's scope) and so is **circuit breaking around the Voyage
call** (only the Anthropic call is wrapped, ADR 0020).

All three datastores now hold real data — Postgres (conversation history), Redis
(its cache), and **Qdrant (document vectors, new in Stage 4)**.

Retrieval is deliberately minimal: no reranking, no hybrid search, no query
expansion, no automatic re-ingestion (an operator runs `scripts/ingest.py`), and
editing a document shorter leaves orphaned tail chunks (ADR 0012). Prompt-injection
defense is the ADR 0014 nonce fence plus Stage 8's heuristic input/excerpt/egress
guardrails (ADR 0019) — still **no ML classifier and no per-source trust tiers**;
those wait on the corpus-admission model changing. Authentication is **API-key
only** — no JWT/OAuth/IdP, no rotation/expiry beyond editing `API_KEYS`, single-tier
authZ, and the rate limiter fails open on a Redis outage (all deferred by decision,
ADR 0019). Secret management is **CI-scanning only** — no runtime secrets backend.

Within the agent stack, **Stage 9 shipped** prompt caching, deterministic context
compaction (windowing, not summarization), and a circuit breaker around the
Anthropic call (ADR 0020). Still deferred: **per-conversation concurrency control**
and **circuit breaking around the Voyage call** (only Anthropic is wrapped).

API versioning (`/v1`) is in place, but **pagination conventions are deferred** —
no endpoint returns a collection yet. Revisit when one does.

---

## Next milestone — Stage 10 (Portfolio)

**Objective:** final polish, docs, demos, and a case-study writeup. No new platform
capability — this stage packages what stages 1–9 built.

**Resolved in Stage 9 (Reliability, ADR 0020):** the reliability surfaces are
closed. A **circuit breaker** wraps the Anthropic `LLMClient` (opens on
transport/5xx after a configurable threshold, fails fast with `503
provider_unavailable`, half-open trial recovery; **never trips on a 400**; above the
SDK's own retries, not instead of them). **Prompt caching** (`cache_control` on the
system prompt + last tool spec, inside `AnthropicClient.stream`) and **deterministic
context windowing** (outbound call only; full history still persisted; not
summarization) close the Stage 3 agent omissions. The **OTel metrics pipeline**
(spanmetrics/servicegraph → Prometheus) now backs Tempo's service map / node graph,
additive to the app's own RED metrics (ADR 0016 unchanged). A **Locust** harness
(two modes: cost-free `test` / opt-in billable `dev`) and a **chaos runbook** are
opt-in and never CI. The **rate-limiter fail-open** is instrumented (counter +
alert), **not reversed** (ADR 0019 stands). **SLOs** (`docs/slo.md`) add two alert
rules to Stage 5's three. Still deferred by decision: per-conversation concurrency
control and circuit breaking around the Voyage call.

**Resolved in Stage 8:** the API is authenticated. `POST /v1/chat/completions`
requires a bearer API key (salted-hash store, constant-time compare, one uniform
`401`); a Redis-backed per-principal rate limiter returns `429` and fails open +
logs on a Redis outage; the three exempt endpoints (`/health`, `/ready`, `/version`,
`/metrics`) stay unauthenticated by design. Two heuristic RAG-hardening guardrails
(input screen that blocks direct override/probe → `400`; log-only excerpt screen)
and a log-only answer-egress check sit on top of the unchanged ADR 0014 nonce fence.
Two hermetic CI gates were added — **gitleaks** (secret scan) and **pip-audit**
(dependency scan), both pinned. The `prod` validator now also requires `API_KEYS` +
`API_KEY_HASH_SECRET`, and the Helm chart wires them into its Secret (ADR 0019).
Deferred by decision: JWT/OAuth/external IdP, role/scope authZ, per-source trust
tiering, and a runtime cloud secrets backend.

**Resolved in Stage 7:** deployment moved off compose-only. The `api` image
deploys to Kubernetes through a Helm chart with liveness/readiness probes on
`/health`/`/ready`, verified end-to-end on a real `kind` cluster (`/ready` reports
all three stores `ok`). Terraform modules (AWS: VPC/EKS/RDS/ElastiCache/S3/Secrets
Manager) are written and **validated, never applied** — no AWS account exists, a
deliberate boundary matching how paid LLM APIs are treated. The dev-vs-managed
datastore split is enforced by the `devDependencies.enabled` flag (ADR 0018). CI
gained `helm`, `terraform`, and `kind`-deploy jobs, all hermetic.

**Resolved in Stage 6:** the `Evaluator` stub is implemented (ADR 0017). The RAG
retrieval pipeline has a two-tier harness run by `scripts/evaluate.py`: a hermetic,
deterministic **Tier 1** (recall@k / MRR over offline-hash embeddings + an
in-memory cosine store) wired into CI as a required regression gate against a
checked-in baseline, and an opt-in, billable **Tier 2** LLM-as-judge (faithfulness
+ citation accuracy) that never runs in CI (same double opt-in as ADR 0015). The
`CLAUDE.md` deferred-table inconsistency was fixed at the same time: **OTel metrics
export moved from Stage 6 to Stage 9**, which owns SLOs and actually needs it —
Stage 6 was always MLOps/eval only per this file.

**Resolved in Stage 5:** the `@traced` seam now has a real OpenTelemetry backend
(spans → OTel Collector → Tempo, ADR 0016), with the profile-keyed hermetic guard
extended to the collector as a third external hop. Grafana dashboards and
symptom-level alerting are provisioned as code. Metrics export through OTel was
**deliberately not built** — the collector carries traces only, Prometheus keeps
`/metrics` (ADR 0016); it is now explicitly a Stage 9 item.

**Resolved in Stage 4 (was carried forward from Stage 3):** the missing contract
test against the real external APIs. Stage 4 added an opt-in, double-gated live
contract test (ADR 0015) that makes one real call each to the Anthropic chat API
and the Voyage embeddings API and asserts the response shapes the code assumes.
It is not run in CI (CI stays key-free and hermetic) — a human runs it
deliberately when changing `llm.py` or `embeddings.py`.

**Resolved in Stage 8 (was the Stage 4 carry-forward):** the ADR 0014 nonce fence
stays the load-bearing injection mitigation, and Stage 8 added a heuristic excerpt
screen and a log-only answer-egress check on top (ADR 0019). Still deferred, and
still tied to the corpus-admission model changing (user uploads, crawls, third-party
feeds): an ML classifier and **per-source trust tiering** — building those against
an unchanged, committer-only corpus would be premature; revisit ADR 0014 if it changes.
