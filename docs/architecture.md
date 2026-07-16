# Architecture

> **Reading rule for this document.** Everything under **Current state** exists
> and is tested today. Everything under **Planned** is **not yet implemented** —
> it is described as intent, not as built software. Do not infer capability from
> a folder existing.
>
> **This file is the source of truth.** `architecture.html` at the repo root is
> generated from it (`uv run python scripts/build_architecture.py`) and a test
> fails if the two drift. Edit this file, never the HTML. Diagrams are
> pre-rendered to inline SVG under `docs/diagrams/` — the page has no CDN and no
> JavaScript, and renders offline (ADR 0010).

**Stage 4 of 10 (RAG).** The platform today is a FastAPI service whose chat
endpoint runs a **real LangGraph agent loop against the Anthropic API**: it
reasons, calls tools, observes the results, and answers — persisting the
conversation to Postgres behind a Redis read-through cache, and reporting the
model's own token counts.

**Stage 4 grounds those answers in documents.** A corpus is chunked and embedded
(Voyage AI) into **Qdrant, which now holds data**; a new `document_search` tool
lets the agent retrieve relevant passages and answer from them, and the response
carries **citations** back to the source chunks. This changed nothing about the
loop's shape: retrieval is one more `Tool` in the registry, and the routes, SSE
framing and `CompletionEngine`/`LLMClient` seams are untouched.

**Stage 2's `EchoEngine` is gone.** The `CompletionEngine` protocol it sat behind
is unchanged, which is what let the Stage 3 swap happen without redesigning the
endpoint — and what let Stage 4 add grounding without touching it either.

---

## Current state (Stage 4 — built and verified)

### Component map

```mermaid
flowchart TD
    client([HTTP client])

    subgraph api["api service (FastAPI)"]
        direction TB
        mw["RequestContextMiddleware<br/>request-id · metrics · access log"]
        routes["routes: health · meta · chat"]
        engine["CompletionEngine<br/>OrchestratorEngine"]
        registry["DatastoreRegistry<br/>pools · lifespan · probe"]

        mw --> routes
        routes -->|"POST /v1/chat/completions"| engine
        routes -->|"GET /ready"| registry
    end

    subgraph agent["agent stack (Stage 3–4)"]
        direction TB
        orch["AgentOrchestrator<br/>history · usage · persistence · citations"]
        loop["AgentGraph (LangGraph)<br/>reason ⇄ act"]
        tools["ToolRegistry<br/>calculator · text_stats · json_query<br/>+ document_search (Stage 4)"]
        llm["LLMClient<br/>AnthropicClient"]

        orch --> loop
        loop --> tools
        loop --> llm
    end

    subgraph retrieval["retrieval stack (Stage 4)"]
        direction TB
        retr["VectorRetriever<br/>embed query · search · score floor"]
        emb["EmbeddingsClient<br/>VoyageEmbeddingsClient"]
        vstore["QdrantVectorStore<br/>ensure · upsert · query"]

        retr --> emb
        retr --> vstore
    end

    store["ConversationStore<br/>Redis read-through → Postgres"]

    pg[("postgres<br/>asyncpg pool<br/>conversations")]
    rd[("redis<br/>redis.asyncio pool<br/>history cache")]
    qd[("qdrant<br/>qdrant-client pool<br/>document vectors")]

    anthropic(["Anthropic API<br/>claude-opus-4-8"])
    voyage(["Voyage AI API<br/>voyage-3.5-lite"])

    client --> mw
    engine --> orch
    orch --> store
    store --> rd
    store --> pg
    llm --> anthropic
    tools -->|"document_search"| retr
    emb --> voyage
    vstore --> qd
    registry --> pg
    registry --> rd
    registry --> qd

    prom["prometheus"] -->|"scrapes /metrics"| api
    graf["grafana"] -->|"datasource"| prom

    classDef external fill:#ede7ff,stroke:#7b5cd6,color:#2c1a66
    classDef store fill:#e8f4ff,stroke:#4a90d9,color:#0a3d62
    class anthropic,voyage external
    class pg,rd,qd store
```

Ingestion (`scripts/ingest.py`) is an **offline operator action**, not part of
the request path: it reads `data/corpus/`, chunks and embeds it, and upserts the
vectors into Qdrant. The `api` service never ingests — it only searches what
ingestion put there.

`shared/` (`config` · `logging` · `observability` · `datastores` · `migrations` ·
`version`) is imported throughout the service rather than sitting in the request
path — it is left out of the diagram above to keep the flow readable.

All three datastores now **hold real data**: Postgres the conversation history,
Redis its cache, and — new in Stage 4 — Qdrant the document vectors. The Stage 2
raw-`httpx` `/readyz` probe is gone; Qdrant is reached through `qdrant-client`,
and its readiness probe is now `get_collections()` (ADR 0012).

### The `api` service

| Endpoint | Purpose | Behaviour today |
|----------|---------|-----------------|
| `GET /health` | Liveness | `200` + service, version, environment. **Touches no datastore.** |
| `GET /ready` | Readiness | `200` `ready` + per-store checks; **`503` `not_ready`** if any configured store is unavailable |
| `GET /version` | Identity | `200` + service, version, environment |
| `GET /metrics` | Prometheus | request count + latency histogram |
| `GET /docs` | OpenAPI UI | generated by FastAPI |
| `POST /v1/chat/completions` | Chat | `200` completion, or an SSE stream when `"stream": true` |

**Request path:** `RequestContextMiddleware` assigns/propagates an
`X-Request-ID` (honouring an inbound header), binds it to a `ContextVar`, times
the request, records Prometheus metrics against the **route template** (not the
raw path, to bound label cardinality), echoes the id on the response, and emits a
structured access log.

**Error handling:** every failure returns one envelope —
`{"error": {"type", "message", "request_id"}}`. Handlers are registered for
`HTTPException` (Starlette's base, so unmatched-route 404s are covered),
`RequestValidationError` (422), and a catch-all `Exception` that logs the full
trace and returns a generic 500 that never leaks internals.

**Lifecycle:** an async `lifespan` logs `service.startup` / `service.shutdown`
and opens/closes the datastore pools.

### Liveness vs readiness

The distinction is load-bearing, not pedantry — see ADR 0005.

```mermaid
flowchart TD
    h["GET /health"] --> hq{"is the process<br/>serving?"}
    hq -->|yes| h200["200 ok"]
    hq -->|no| hdead["no response<br/><i>→ orchestrator restarts</i>"]

    r["GET /ready"] --> probe["probe every configured store<br/><i>concurrently, each timeout-bounded</i>"]
    probe --> rq{"any store<br/>unavailable?"}
    rq -->|no| r200["200 ready<br/>{postgres:ok, redis:ok, qdrant:ok}"]
    rq -->|yes| r503["503 not_ready<br/><i>→ traffic routed away,<br/>container NOT killed</i>"]

    note["/health never probes a dependency:<br/>a database blip must not become<br/>a pod restart storm"]

    classDef bad fill:#ffe9e9,stroke:#d95a5a,color:#7a0000
    classDef good fill:#e9f7ec,stroke:#4caf7d,color:#0a4a2a
    classDef nte fill:#f4f4f4,stroke:#999,color:#333
    class r503,hdead bad
    class h200,r200 good
    class note nte
```

### The agent loop

Both transports run the **same** LangGraph graph, so streaming is a transport
choice and cannot change the answer. `reason` calls the model; `act` executes
every requested tool and feeds the results back; the loop ends when the model
answers instead of calling a tool, or when the step cap trips. See ADR 0006.

```mermaid
flowchart LR
    start([task]) --> reason["reason<br/><i>call the model</i>"]
    reason --> decide{"asked for<br/>a tool?"}
    decide -->|no| answer([final answer])
    decide -->|"yes, and steps < max"| act["act<br/><i>execute tools</i>"]
    decide -->|"yes, but step cap hit"| answer
    act -->|"observe: tool_result"| reason

    classDef good fill:#e9f7ec,stroke:#4caf7d,color:#0a4a2a
    classDef node fill:#e8f4ff,stroke:#4a90d9,color:#0a3d62
    class answer good
    class reason,act node
```

A tool that fails returns an `is_error` result rather than raising: the model
asked for something that did not work, and the useful response is to tell it so
it can correct itself — not to throw away a run the caller already paid for.

### Chat completion and streaming

SSE framing is specified in ADR 0004 and is **unchanged** from Stage 2. What
changed is `usage`: it now carries the model's own counts, summed across every
model call the run made.

The sequence below shows a **grounded** turn: the model calls `document_search`,
which embeds the query with Voyage and searches Qdrant, and the run answers from
the fenced excerpts. The citations accumulate in graph state and are surfaced on
the response (ADR 0013).

```mermaid
sequenceDiagram
    participant C as client
    participant R as routes/chat.py
    participant O as AgentOrchestrator
    participant G as AgentGraph
    participant A as Anthropic API
    participant T as ToolRegistry
    participant V as VectorRetriever
    participant Q as Voyage + Qdrant

    C->>R: POST /v1/chat/completions
    Note over R: Pydantic validation<br/>≥1 message · role enum<br/>max_tokens > 0

    R->>O: complete / stream
    O->>O: load history (Redis → Postgres)

    O->>G: run(messages)
    G->>A: messages.stream(tools=[...])
    A-->>G: tool_use: document_search("roll back?")
    G->>T: invoke
    T->>V: retrieve(query)
    V->>Q: embed query · search vectors
    Q-->>V: scored chunks
    V-->>T: RetrievedDocuments
    T-->>G: fenced excerpts + citations
    G->>A: messages.stream(+ tool_result)
    A-->>G: text: "Shift traffic to the previous version."

    O->>O: persist turn · invalidate cache

    alt stream = false
        R-->>C: 200 {choices, usage, citations}
    else stream = true
        R-->>C: data: {delta:{role,content}}
        R-->>C: data: {delta:{}, finish_reason, citations}
        R-->>C: data: [DONE]
    end
```

### Retrieval, grounding and citations (Stage 4)

Retrieval is composed as one more capability the agent can call, not a new loop.
`ToolRegistry.default()` still returns the three offline tools; the retrieval
tool is added at wiring time (`ToolRegistry.with_tools`, from `services.api.app`)
only when there is a live Qdrant to search. The dependency runs retrieval →
agents, so the agent kernel knows what a tool *is*, not what retrieval talks to.

| Piece | Module | Role |
|-------|--------|------|
| Ingestion | `services/retrieval/ingest.py` | `load_corpus` → `chunk_documents` (LlamaIndex `SentenceSplitter`) → embed → `upsert`. Offline; run by `scripts/ingest.py`. |
| Embeddings | `services/retrieval/embeddings.py` | `VoyageEmbeddingsClient`, and the offline `HashingEmbeddingsClient` the `test` profile runs — same hermetic guard as Anthropic (ADR 0009, 0011). |
| Vector store | `services/retrieval/store.py` | `QdrantVectorStore` on `qdrant-client`; deterministic UUIDv5 point ids make re-ingest idempotent (ADR 0012). |
| Retriever | `services/retrieval/retriever.py` | `VectorRetriever`: embed the query, search, drop matches below a score floor so an unrelated question returns nothing rather than weak noise. |
| Tool | `services/retrieval/tool.py` | `document_search`: the injection boundary. |

**Prompt-injection boundary (ADR 0014).** This is the first tool whose result is
not a pure function of its arguments — it returns document text that could be
attacker-influenced. Retrieved excerpts are fenced with a **per-call random
nonce** and labelled, next to the data, as untrusted reference material that must
not be obeyed as instructions. The nonce is unguessable, so a document cannot
close the fence and appear to break out into instruction context. This removes
ambiguity about what is data; it is **not** immunity, and the residual risk is
documented and deferred to Stage 8, which owns security.

**Citations (ADR 0013).** Retrieved chunks are carried out of the tool as typed
`Citation` data — never parsed back out of the text the model saw, so a document
cannot forge its own provenance. They accumulate across the run (deduplicated by
chunk id) and surface as a **new top-level `citations` field** on the response:
on the final SSE frame when streaming, once when whole. An ungrounded answer
reports `"citations": []` rather than omitting the field.

### `shared/` foundation

| Module | Responsibility |
|--------|----------------|
| `config.py` | Typed `Settings` (pydantic-settings), layered profiles — see ADR 0003. `prod` refuses to boot without all three datastore URLs, `ANTHROPIC_API_KEY`, *and* `VOYAGE_API_KEY` |
| `logging.py` | JSON formatter (`timestamp`, `level`, `service`, `environment`, `request_id`, `logger`, `message`, `exception`) + console formatter; routes uvicorn logs through one handler |
| `observability.py` | `@traced` — logs span enter/exit/duration/error; sync + async; PEP 695 typed |
| `datastores.py` | `Datastore` ABC + Postgres/Redis/Qdrant implementations and `DatastoreRegistry` — see ADR 0005 |
| `migrations.py` | Forward-only raw-SQL migration runner, applied in the lifespan — see ADR 0007 |
| `version.py` | `__version__`, kept in sync with `pyproject.toml` by a test |

### Datastore lifecycle

```mermaid
stateDiagram-v2
    [*] --> Unconfigured: no URL
    [*] --> Configured: URL set

    Unconfigured --> NotConfigured: never dialled
    NotConfigured --> [*]: reports "not_configured"<br/>does not block readiness

    Configured --> Connecting: lifespan startup<br/>(all stores concurrently)
    Connecting --> Connected: pool opened
    Connecting --> Failed: connect raised<br/><i>logged, NOT propagated</i>

    Connected --> Connected: ping ok → "ok"
    Connected --> Blip: ping fails → "unavailable"
    Blip --> Connected: driver pool reconnects

    Failed --> Failed: reports "unavailable"<br/>until process restart

    Connected --> [*]: shutdown closes pool
    Failed --> [*]: shutdown
```

Startup **never raises**: an unreachable store yields a diagnosable un-ready pod
rather than a crash loop. Connects run concurrently — serialised, three dead
stores delayed `/health` by ~20s (measured), long enough to trip a liveness probe
and cause the very crash loop the design avoids.

### Conversation state and caching

Postgres owns the data; Redis only ever holds a copy. Losing Redis costs latency,
never history. Writes **invalidate** the cache rather than rewriting it —
deleting cannot disagree with Postgres, whereas computing the new value twice
can. See ADR 0008.

```mermaid
flowchart TD
    req["chat request<br/>with conversation_id"] --> load{"history in<br/>Redis?"}
    load -->|hit| use["use cached history"]
    load -->|"miss or Redis down"| pgread[("read Postgres")]
    pgread --> populate["populate cache (TTL)"]
    populate --> use
    use --> run["run the agent loop"]
    run --> write[("append turn to Postgres")]
    write --> inval["DELETE the cache entry"]

    note["Postgres first, invalidate second:<br/>the other order lets a concurrent read<br/>re-cache the pre-append history"]

    classDef store fill:#e8f4ff,stroke:#4a90d9,color:#0a3d62
    classDef nte fill:#f4f4f4,stroke:#999,color:#333
    class pgread,write store
    class note nte
```

A request **without** a `conversation_id` is stateless and touches neither store
— the Stage 2 contract (client sends the full history) still holds exactly.

**Schema** (`migrations/0001_conversations.sql`, applied on startup):

| Table | Purpose |
|-------|---------|
| `conversations` | One row per conversation: `id`, `created_at`, `updated_at` |
| `conversation_messages` | `conversation_id` -> `position` · `role` · `content`, unique on `(conversation_id, position)`, cascading delete |
| `schema_migrations` | Applied versions; what makes the runner idempotent |

### Infrastructure (Docker Compose)

`api` (built, non-root, multi-stage), `postgres`, `redis`, `qdrant`,
`prometheus` (scrapes `api:8000/metrics`), `grafana` (Prometheus datasource
provisioned as code). Postgres and Redis gate `api` startup via healthchecks.
Grafana is on host port **3001** (3000 collides with an unrelated local
container — see CLAUDE.md).

### Quality gate

ruff (lint + format) · mypy `strict` · pytest (246 tests, plus opt-in
live-datastore, live-Qdrant and live-provider layers that skip by default) ·
pre-commit · GitHub Actions running the same commands, **plus** a Docker job that
boots the container against live postgres/redis/qdrant service containers and
fails unless `/ready` reports every store `ok` and the chat + SSE contract holds.

The suite is **hermetic by construction**: the `test` profile cannot construct a
real Anthropic client *or a real Voyage client* — both refuse before their key is
read — so no test can make a paid API call regardless of what is in the
environment (ADR 0009, 0011). CI needs neither `ANTHROPIC_API_KEY` nor
`VOYAGE_API_KEY`. A separate, double-opt-in contract test (ADR 0015) makes one
real call to each provider when a human runs it deliberately.

---

## Planned — not yet implemented

Each item below is a **contract or empty folder only** today. The owning stage
builds it.

| Component | Stage | Status today |
|-----------|-------|--------------|
| `services/monitoring` — `SpanExporter`; OpenTelemetry export, Grafana dashboards, alerts | 5 | **Not implemented.** `@traced` logs only; no OTel backend, no dashboards. |
| `services/evaluation` — `Evaluator` ABC | 6 | **Not implemented.** Raises. |
| `infrastructure/kubernetes`, `infrastructure/terraform` | 7 | **Empty placeholders.** Compose only today. |
| `services/security` — `AuthProvider`, `Guardrail` | 8 | **Not implemented.** API is entirely unauthenticated. |
| Reliability — load testing, chaos, SLOs, pool tuning, reconnect/circuit breaking | 9 | **Nothing exists.** |

### Deliberate non-goals for Stage 4

**No authentication** (the corpus and the API are both unauthenticated — Stage 8),
no OTel backend, no evaluation, no Kubernetes. Pagination conventions are still
deferred — no endpoint returns a collection yet.

Retrieval itself is deliberately minimal: no reranking, no hybrid (keyword +
vector) search, no query expansion, and no automatic re-ingestion — ingestion is
an explicit operator action. Editing a document that becomes shorter leaves its
old tail chunks in Qdrant, since ingestion is upsert-only (ADR 0012). The
prompt-injection mitigation is delimiting and labelling, **not** full hardening:
no classifier, no trust tiers, no answer egress filtering (ADR 0014, Stage 8).

Within the agent stack, the Stage 3 omissions still stand: no prompt caching, no
context compaction, no per-conversation concurrency control, and no retry or
circuit breaking around the Anthropic *or Voyage* call beyond the SDKs' defaults.
Stage 9 owns the reliability of those calls.

---

## Key architectural properties

**Stable seams.** `@traced` is the tracing seam (Stage 5 makes it emit OTel spans
without touching a single call site). `get_settings()` is the config seam.
`CompletionEngine` is the model seam — Stage 3 proved it, and **Stage 4 leaned on
it again**: retrieval and citations were added and the endpoint, SSE framing and
wire format still did not change shape (the wire *gained* a `citations` field but
kept everything else). `LLMClient` is the model-provider seam, and `Tool` is the
capability seam — retrieval slotted in as one more `Tool`, not a new loop.
`EmbeddingsClient` is a new provider seam, hermetic by the same construction as
`LLMClient` (ADR 0011). `Datastore` is the storage seam — **Stage 4 made good on
its promise** and swapped Qdrant's HTTP probe for `qdrant-client` behind the
unchanged contract. Later stages fill seams; they do not re-cut them.

**Fail loud.** Invalid config fails at startup — `prod` will not boot without its
datastore URLs, its `ANTHROPIC_API_KEY`, *or its `VOYAGE_API_KEY`*. Unbuilt
components raise `NotImplementedError`. Errors are surfaced and logged with
traces, never swallowed. The deliberate exceptions are places where degrading
beats failing: a datastore down at boot yields an un-ready pod rather than a
crash loop (ADR 0005); a Redis failure costs a cache hit rather than the request
(ADR 0008); and a retrieval below the score floor returns no sources rather than
weak noise the agent would cite (ADR 0013).

**Honest health.** `/ready` reflects reality: it dials its dependencies and says
503 when they are down. `/health` answers only "is this process alive?".

**Reproducibility.** Exact pins + committed `uv.lock` + `--frozen` installs mean
laptop, CI and image resolve identically. `link-mode = "copy"` stops uv's
cross-drive hardlink install from silently skipping a package.

**Security posture from day one.** No secrets in git (enforced by test +
`.gitignore`), credentials — including `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` —
from env only, container runs as non-root, internal error text never reaches
clients. The calculator tool parses to an AST and walks an allow-list rather than
calling `eval`; the retrieval tool fences untrusted document text behind a
per-call nonce (ADR 0014): the model is not a trusted caller, and neither is
whatever talked to it or whatever it retrieved. **The API is still entirely
unauthenticated** — that is Stage 8.

## See also

- [ADR 0001 — Stack selection](adr/0001-stack-selection.md)
- [ADR 0002 — Repository structure](adr/0002-repo-structure.md)
- [ADR 0003 — Configuration approach](adr/0003-configuration-approach.md)
- [ADR 0004 — Streaming transport](adr/0004-streaming-transport.md)
- [ADR 0005 — Datastore connection pooling and readiness](adr/0005-datastore-connection-pooling.md)
- [ADR 0006 — Agent loop and orchestration](adr/0006-agent-loop-and-orchestration.md)
- [ADR 0007 — Database migrations](adr/0007-database-migrations.md)
- [ADR 0008 — Conversation caching strategy](adr/0008-conversation-caching-strategy.md)
- [ADR 0009 — Hermetic LLM testing](adr/0009-hermetic-llm-testing.md)
- [ADR 0010 — Pre-rendered architecture diagrams](adr/0010-pre-rendered-diagrams.md)
- [ADR 0011 — Embeddings via Voyage AI](adr/0011-embeddings-provider.md)
- [ADR 0012 — Qdrant collection design and `qdrant-client`](adr/0012-qdrant-vector-store.md)
- [ADR 0013 — Citation shape](adr/0013-citation-shape.md)
- [ADR 0014 — Prompt-injection mitigation for retrieved text](adr/0014-prompt-injection-mitigation.md)
- [ADR 0015 — Opt-in live provider contract test](adr/0015-live-provider-contract-test.md)
- [PROJECT_STATUS.md](PROJECT_STATUS.md) — roadmap and progress
