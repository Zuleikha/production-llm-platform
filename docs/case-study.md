# Case study — a production-grade LLM platform, built in ten stages

**Audience:** an ML / AI platform engineering reviewer assessing production rigor.
**Claim:** this repository is not a demo of an LLM call — it is a demonstration of
the *engineering around* an LLM call: the seams, the failure modes, the tests, the
deployment, and the decisions that were deliberately **not** taken. This document
tells that story through the decision trail (`docs/adr/`), not as a feature list —
`PROJECT_STATUS.md` and `docs/architecture.md` already hold the feature-by-feature
state.

Everything below is backed by a committed ADR, a test, or a log line. Where
something was deliberately not built, it says so as plainly as `PROJECT_STATUS.md`
does.

---

## What it is

A FastAPI service exposing one substantive endpoint —
`POST /v1/chat/completions`, OpenAI-shaped — behind which sits a **real LangGraph
agent loop against the Anthropic API** (`claude-opus-4-8`): reason → call a tool →
observe → answer, bounded by a step cap, with the model's own token counts summed
across the run. Answers can be **grounded** in a document corpus (LlamaIndex
chunking → Voyage embeddings → Qdrant) and carry typed **citations**. Conversation
state persists to Postgres behind a Redis read-through cache. The whole thing is
authenticated, rate-limited, traced, deployed to Kubernetes via Helm, and wrapped
in a circuit breaker.

The point of interest for a reviewer is not any one of those — it is that each was
added **without re-cutting a seam**, and each came with a decision recorded about
the trade-off accepted.

## The through-line: stable seams, filled in stage by stage

The architecture was cut once, in Stages 1–2, around a handful of protocols:
`CompletionEngine` (the model seam), `LLMClient` (the provider seam), `Tool` (the
capability seam), `EmbeddingsClient`, `Datastore`, `get_settings()` (config), and
`@traced` (observability). **Every later stage filled a seam; none re-cut one.**
That is the load-bearing claim, and it is checkable:

| Stage | Production concern added | Seam it leaned on | Evidence |
|:--:|--------------------------|-------------------|----------|
| 1 | Foundation, config, logging, `@traced` | — | ADR 0001–0003 |
| 2 | HTTP API, SSE, pooled datastores, readiness | `CompletionEngine`, `Datastore` | ADR 0004–0005 |
| 3 | Real agent loop (LangGraph + Anthropic), conversation state | `CompletionEngine` swap — routes unchanged | ADR 0006–0009 |
| 4 | RAG grounding + citations | retrieval slotted in as one more `Tool` | ADR 0011–0014 |
| 5 | Distributed tracing → Tempo | `@traced` — not one of ~30 call sites changed | ADR 0016 |
| 6 | RAG evaluation + CI regression gate | operator-script shape (like ingest) | ADR 0017 |
| 7 | Kubernetes (Helm) + Terraform | probes on the existing `/health`/`/ready` | ADR 0018 |
| 8 | AuthN, rate limiting, RAG guardrails, secret scanning | middleware over the one endpoint | ADR 0019 |
| 9 | Circuit breaker, prompt caching, context windowing, SLOs | `LLMClient` wrapper — wire shape unchanged | ADR 0020 |
| 10 | Portfolio: docs, demo, this case study | — | — |

When Stage 3 replaced Stage 2's `EchoEngine` with a real agent, the routes, the
schemas and the SSE framing did not change shape. When Stage 4 added grounding, the
wire *gained* a `citations` field and nothing else moved. When Stage 5 gave
`@traced` a real OpenTelemetry backend, the ~30 decorated functions were untouched —
the SDK was installed underneath them via the global provider. That is what a seam
is *for*, and it is the single thing this repo is built to show.

## The decisions most worth a reviewer's attention

### 1. LangChain was rejected, twice, for the same reason

Stage 3 uses `langgraph` for the agent state machine but **not** `langchain`: the
graph calls the Anthropic SDK directly, so LangChain's core abstractions buy
nothing and cost a large transitive tree (ADR 0006). Stage 4 made the identical
call against the `llama-index` meta-package — it pulls in a *rival vendor's* SDK
(`llama-index-llms-openai`) to run an Anthropic + Voyage stack — and promoted only
`llama-index-core`, from which just the two ingestion primitives are used (ADR
0011). Both are in `pyproject.toml` as commented reasoning, not folklore. The
signal: dependencies are chosen for what is on the request path, not for what a
tutorial reaches for.

### 2. The hermetic-seam pattern, reused across three external vendors

The most reused idea in the codebase. The `test` profile **cannot construct** a
real client for any paid external hop: `AnthropicClient` (ADR 0009),
`VoyageEmbeddingsClient` (ADR 0011), *and* `OTLPTracerProvider` (ADR 0016) each
**raise in their constructor** under the `test` profile, before an endpoint or key
is read. The guard is keyed on the *profile*, not on the key's absence — so no test
can spend money or dial out regardless of what is in the environment, and CI needs
no `ANTHROPIC_API_KEY`, no `VOYAGE_API_KEY`, and no collector. The escape hatch is a
single, double-gated live contract test (`RUN_LIVE_CONTRACT_TESTS=1` + a key) that a
human runs deliberately when changing `llm.py`/`embeddings.py` (ADR 0015), because
the hermetic suite *cannot* catch a wrong belief about the real API's shape. This is
the pattern a reviewer should weigh: the same structural guard, applied uniformly,
the moment a third vendor appeared.

### 3. The rate limiter fails **open**, on purpose

A Redis outage makes the per-principal rate limiter **fail open** — it allows the
request and logs `ratelimit.degraded` (ADR 0019, on the ADR 0008 precedent). This is
a deliberate availability-over-enforcement choice for a rate limiter that is a cost
control, not a security boundary: a Redis blip should not take down a working,
*authenticated* endpoint. It is not silent — Stage 9 added a
`rate_limiter_fail_open_total` counter and an SLO alert so the degradation is
visible (ADR 0020). The same fail-toward-availability judgement recurs: a datastore
down at boot yields an un-ready pod, not a crash loop (ADR 0005); a Redis cache miss
degrades to a Postgres read, the one sanctioned exception to fail-loud (ADR 0008); a
retrieval below the score floor returns no sources rather than weak noise (ADR 0013).
Each is a *stated* exception to the project's otherwise strict fail-loud rule.

### 4. Terraform is validated, never applied

The AWS Terraform (VPC / EKS / RDS / ElastiCache / S3 / Secrets Manager) is
**written and validated but never applied** — `init` + `validate` + `fmt -check` are
the CI-safe gate; `plan`/`apply` need real AWS credentials this project does not have
(ADR 0018). This is the *same boundary* the project draws around paid LLM APIs: the
code is real and checkable, the spend is not incurred. A reviewer gets to see the
infrastructure-as-code competence without the repo pretending to a cloud account it
lacks. The Kubernetes path is not merely templated, though — it is verified
end-to-end on a real local `kind` cluster where `/ready` reports all three
datastores `ok`.

### 5. Retrieved text is treated as untrusted, structurally

The first tool whose output is not a pure function of its input — `document_search`
— returns text that could be attacker-influenced. It is fenced behind a **per-call
random nonce** and labelled as untrusted reference data (ADR 0014); citations are
carried out as *typed* data, never parsed back out of the text the model saw, so a
document cannot forge its own provenance (ADR 0013). Stage 8 added heuristic
input/excerpt/egress guardrails *on top* — but the ADR explicitly keeps the nonce
fence as the load-bearing control and calls the heuristics evadable signal, not a
boundary (ADR 0019). The honesty about what is and isn't a real mitigation is the
point.

## What is deliberately **not** built

Pulled from `PROJECT_STATUS.md`'s standing deferred lists — stated here so a
reviewer does not have to infer scope from absence:

- **Per-conversation concurrency control.** Two concurrent turns on one
  `conversation_id` collide on `(conversation_id, position)`; the second fails
  loudly rather than being serialised (ADR 0008's known gap, out of Stage 9's
  scope).
- **Circuit breaking around the Voyage call.** Only the Anthropic call is wrapped;
  a Voyage outage degrades to ungrounded answers already (ADR 0020).
- **Context compaction is windowing, not summarization** — a long conversation
  drops its oldest turns from the *outbound* model call (full history still
  persisted), rather than paying for a second summarizing model call (ADR 0020).
- **Auth is API-key only** — no JWT / OAuth / external IdP, no rotation/expiry
  beyond editing `API_KEYS`, single-tier authZ (ADR 0019).
- **Secret management is CI-scanning only** (gitleaks + pip-audit) — no runtime
  secrets backend (ADR 0019).
- **Retrieval is deliberately minimal** — no reranking, no hybrid search, no query
  expansion, no automatic re-ingestion; editing a document shorter orphans its tail
  chunks, since ingestion is upsert-only (ADR 0012).
- **No ML injection classifier and no per-source trust tiers** — those wait on the
  corpus-admission model changing from committer-only (ADR 0014/0019).
- **Evaluation covers RAG retrieval only** (recall@k / MRR) — agent tool-use and
  open-ended chat quality have no fixture corpus to grade against (ADR 0017).
- **Pagination conventions** — deferred until an endpoint returns a collection.

## Known open issues carried into the final state

Stated, not buried — the same posture `PROJECT_STATUS.md` takes:

- **`request_id: null` on the Postgres-down `500`.** When Postgres is down
  mid-conversation (chaos-runbook scenario 1), the resulting `500` carries a null
  `request_id` instead of the request's id. It is almost certainly pre-existing
  (predates Stage 9's reliability work) and was **explicitly not chased down** — the
  fix is not worth the Stage 10 scope, and it is recorded here as an accepted,
  deferred item rather than silently dropped.
- **`/ready` does not check schema version** — a failed migration leaves the service
  reporting ready while every chat query fails (ADR 0007).
- **A datastore that fails `connect` at boot stays `unavailable` until restart** —
  there is no runtime reconnect of a pool that never opened.
- **The `422` envelope stringifies Pydantic's raw error list** into `message`.

## How to see it working

- `docs/demo.md` — a scripted walkthrough (`scripts/demo.sh`) with **real captured
  output**: authenticated chat, SSE streaming, the citations field, the uniform
  `401`, a real `429`, and the circuit-breaker contract.
- `docs/architecture.md` — the visual architecture (regenerated `architecture.html`
  at the repo root), including a single request-lifecycle diagram showing every
  production gate a chat request passes.
- `docs/adr/` — the twenty decisions, each with the trade-off it accepted.
- `docs/verification-log/` — each stage's *independent* verification, separate from
  the build-time self-report.
- The load story: ADR 0020 addendum 3 records a real Locust run (60 users) against
  the `test` stack — zero pool timeouts, a sub-150 ms chat p99, and the
  per-principal rate limiter cutting in exactly at threshold.
