# CLAUDE.md — project memory

Read at the start of every session. Keep under ~150 lines. Facts only — rationale
lives in `docs/adr/`.

## Current state — **Stage 4 of 10 (RAG), COMPLETE.** Version `0.1.0`

`POST /v1/chat/completions` runs a **real LangGraph agent loop against the Anthropic
API** (`claude-opus-4-8`): reason → tool → observe → answer, bounded by
`agent_max_steps`. Real token usage summed across model calls. History persists to
Postgres behind a Redis cache when a `conversation_id` is sent; else stateless as in
Stage 2. **Stage 4 grounds answers:** a corpus (`data/corpus/`) is ingested
(LlamaIndex chunking → Voyage embeddings → **Qdrant, now holding data**); a
`document_search` tool retrieves passages and the response gains a top-level
**`citations`** field. Routes, SSE format, `CompletionEngine`/`LLMClient` seams
**unchanged** — retrieval is one more `Tool`. **No auth** — stub (Stage 8).

Roadmap **`docs/PROJECT_STATUS.md`** (canonical). Detail
`docs/stage-summaries/stage-0{1-foundation,2-api,3-agents,4-rag}.md`. Rationale
**`docs/adr/`**: 0001 stack · 0002 structure · 0003 config · 0004 streaming · 0005
pooling · 0006 agent loop · 0007 migrations · 0008 caching · 0009 hermetic LLM · 0010
diagrams · 0011 embeddings/Voyage · 0012 Qdrant · 0013 citations · 0014 injection ·
0015 live contract test.

## Layout

```
services/api/    app.py · routes/{health,meta,chat} · schemas.py
                 completions.py = CompletionEngine seam + OrchestratorEngine
services/agents/ base.py (Agent · ToolAgent) · tools.py (registry, 3 offline tools)
services/orchestrator/  base.py · graph.py (LangGraph) · llm.py (LLMClient) · conversations.py
services/retrieval/  embeddings.py (seam) · store.py (Qdrant) · ingest.py · retriever.py ·
                 tool.py (document_search — injection boundary)
services/{evaluation,monitoring,security}/  ⬜ stubs
shared/          config · logging · observability · datastores · migrations · version
data/corpus/     RAG corpus (generic engineering .md); scripts/ingest.py loads it
migrations/      NNNN_name.sql forward-only, applied in lifespan · tests/ mirrors source
docs/diagrams/   GENERATED SVG · architecture.html GENERATED from architecture.md
```

## Conventions

- **Stubs:** future-stage folders get a `README.md` (contract + owning stage) plus an
  ABC/Protocol whose methods `raise NotImplementedError`. Never a mini-version.
- **Naming:** `snake_case` funcs, `PascalCase` classes, `_private`. ADRs `NNNN-kebab-title.md`.
  Stage summaries + verification logs have **fixed** filenames (see PROJECT_STATUS).
- **Config:** one `Settings` (pydantic-settings) via `get_settings()` (`lru_cache`d).
  Precedence: OS env > `.env` > `config/environments/<ENV>.env` > defaults. **No secrets
  committed** — OS env only (a test enforces it). **`prod` requires all three datastore
  URLs + `ANTHROPIC_API_KEY` + `VOYAGE_API_KEY`**; `test` ignores the root `.env`.
- **Datastores:** `DatastoreRegistry.from_settings()` on `app.state.datastores`. No URL =
  `not_configured`, never dialled. `startup()` concurrent and **never raises** — failures
  surface on `/ready`, not a crash loop. `/health` must **never** probe (tripwire). Qdrant
  uses `qdrant-client` (probe = `get_collections()`).
- **Paid-provider seams — read ADR 0009/0011 first:** the `test` profile **cannot
  construct** `AnthropicClient` *or* `VoyageEmbeddingsClient`; each raises before its key
  is read. Go via `build_llm_client` / `build_embeddings_client`. The guard keys on the
  *profile*, not the key's absence, so a dev with keys exported gets CI's hermetic suite.
- **No sampling params.** Claude Opus 4.7+ **rejects `temperature`/`top_p`/`top_k` with a
  400**; `budget_tokens` gone (use `thinking={"type":"adaptive"}`). `max_tokens` **required**.
  `ChatCompletionRequest.temperature` accepted for wire compat, **not forwarded**.
- **Agent:** routes call the `CompletionEngine` on `app.state.engine`; `create_app(...,
  engine=...)` overrides it (stops the lifespan rebuilding it). `ToolRegistry.default()` = 3
  offline tools; `document_search` added via `with_tools` when Qdrant is up. `Tool.run` is
  **async**. `calculator` walks an AST allow-list, **never `eval`**. Failing tool → `is_error`.
- **Retrieval (ADR 0011-0014):** ingestion is an operator action (`scripts/ingest.py`), never
  a boot hook — **costs money** (Voyage) outside `test`. Qdrant point ids are UUIDv5 of the
  chunk id (server rejects arbitrary strings; re-ingest idempotent). **Retrieved text is
  untrusted** — `document_search` nonce-fences excerpts; citations are typed, never parsed from
  text. Delimiting ≠ immunity (Stage 8); widening the corpus changes the threat model.
- **Persistence:** Postgres is source of truth, Redis only caches. Writes **invalidate**
  (Postgres first, then `DELETE`) — never rewrite. A Redis failure degrades to a Postgres
  read: the **one** sanctioned exception to fail-loud (ADR 0008). Migrations are plain SQL,
  forward-only, advisory-locked.
- **Logging:** `get_logger(__name__)`; log **events** not sentences —
  `_logger.info("http.request", extra={...})`. One JSON object/line. Never log
  PII/secrets/tokens (nor queries/excerpts — attacker-influenced). **Tracing:** `@traced` on new
  app functions; **exempt:** `shared/{logging,observability}.py` (recursion), async
  generators/lifespan, LangGraph nodes.
- **Errors:** fail loud. Envelope `{"error": {type, message, request_id}}`; 500s never leak
  internals. Handlers on **Starlette's** `HTTPException`.
- **Types:** mypy `strict`, everything annotated incl. tests. Narrow with `isinstance` not
  `# type: ignore`. Depend on narrow Protocols not concrete drivers. **Deps:** `==` pins,
  `uv.lock` committed, `--frozen` installs.
- **Tests:** `tests/unit/`, mirror source. Test contracts, not internals. Write the failing
  test first; never edit a test to make it pass. Switching profiles needs
  `monkeypatch.setenv` **+** `get_settings.cache_clear()`. Opt-in layers skip by default:
  `TEST_DATABASE_URL`/`TEST_REDIS_URL`, `TEST_QDRANT_URL`, `RUN_LIVE_CONTRACT_TESTS=1` + keys.
- **Container boot is required, not deferrable.** Before self-report, `docker build` and run
  the image in **both** `prod` and `test` profiles, and curl the stage's endpoints. A green
  `pytest` does not prove the container boots: Stage 3 deferred this and CI caught two failures
  a 2-min local boot catches (`docs/verification-log/stage-03-agents.md`).
- **Architecture doc:** `docs/architecture.md` is the source; `architecture.html` is
  **generated** by `scripts/build_architecture.py` — never edit the HTML. Diagrams are
  **pre-rendered SVG** in `docs/diagrams/` — no CDN, no JS (ADR 0010). Rendering one needs
  Node/`npx`; building + `--check` need only Python. A Mermaid keyword (`graph`, `end`) as a
  node id fails the render. **Every stage updates the md + regenerates** (a test fails on drift).

## Run / test / lint

```bash
uv sync                                        # install
export ANTHROPIC_API_KEY=sk-ant-... ; export VOYAGE_API_KEY=pa-...   # real calls only; never dev/test
uv run uvicorn services.api.app:app --reload   # API only  -> :8000
docker compose up -d --build                   # full stack
uv run python scripts/ingest.py                # ingest corpus -> Qdrant (costs $ outside test)
pwsh scripts/verify.ps1   # or ./scripts/verify.sh — the whole gate (ruff · format · mypy · pytest)
uv run python scripts/build_architecture.py   # regenerate (needs npx for diagrams)
docker build -t plp/api:local . && docker run -d -p 8010:8000 -e ENVIRONMENT=test plp/api:local
```

Endpoints: `/health` `/ready` `/version` `/metrics` `/docs` · `POST /v1/chat/completions`
(`"stream": true` SSE; optional `conversation_id`; response has `citations`). Grafana :3001.

## Known environment quirks — this machine, not the code

- **Cross-drive uv / lock-install mismatch.** uv cache on `C:`, repo on `D:`. uv's default
  hardlink mode fails across drives **silently** — the package stays in `uv.lock` but never
  lands in the venv, surfacing as a baffling `ModuleNotFoundError`. Fixed repo-wide by
  `link-mode = "copy"` under `[tool.uv]` — do not remove. If a locked package won't import,
  diff `uv pip list` against `uv.lock` first. `sniffio` is an explicit direct dep from this.
- **Never use the system Python at `D:\Python\Python312`.** Stripped build: a venv from it
  **segfaults on `import ctypes`** (surfaces as an access violation importing `httpx`). Use
  `uv python install 3.12 && uv venv --managed-python --python 3.12`; runs CPython **3.12.13**.
- **Grafana port 3000 conflicts with `open-webui`.** Remapped to **3001** in
  `docker-compose.yml`; check `docker ps` for collisions first. **Docker Desktop must be
  running** for live-datastore/Qdrant tests — `docker ps` is the real check, not
  `docker compose version`.

## Deferred — do NOT build early

| Stage | Deferred |
|:--:|---|
| 5 | OTel export, Grafana dashboards, alerting · **6** Evaluation (`Evaluator`), MLOps pipelines |
| 7 | K8s manifests, Terraform · **8** Auth, guardrails, rate limiting, RAG injection hardening — **API unauthenticated** |
| 9 | Load/chaos testing, SLOs, pool tuning, circuit breaking, prompt caching, context compaction · **10** Portfolio polish |

## Known issues

- Starlette 1.3.1 deprecates `httpx` for `TestClient` (warning only). 422 stringifies
  Pydantic's raw error list into `message`. `config/environments/*.env` can drift from
  `.env.example`. A datastore failing `connect` at boot stays `unavailable` until restart.
- **`/ready` does not check schema version** — a failed migration leaves the service
  reporting ready while every chat query fails (ADR 0007).
- **No per-conversation concurrency control** — two concurrent turns on one `conversation_id`
  collide on `(conversation_id, position)`; the second fails loudly, not serialised (ADR 0008).
- **Editing a doc shorter orphans its tail chunks** in Qdrant — ingestion is upsert-only (ADR 0012).
- **The hermetic suite cannot catch a wrong assumption about the real APIs** — the fakes
  encode our beliefs. Run the opt-in live contract test (ADR 0015) when changing `llm.py`/`embeddings.py`.
