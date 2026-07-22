# CLAUDE.md — project memory

Read at the start of every session. Keep under ~150 lines. Facts only — rationale
lives in `docs/adr/`.

## Current state — **Stage 7 of 10 (Kubernetes), COMPLETE.** Version `0.1.0`

`POST /v1/chat/completions` runs a **real LangGraph agent loop against the Anthropic
API** (`claude-opus-4-8`): reason → tool → observe → answer, bounded by `agent_max_steps`;
real usage summed across calls. History persists to Postgres behind a Redis cache with a
`conversation_id`, else stateless. **Stage 4 grounds answers:** corpus ingested (LlamaIndex →
Voyage → **Qdrant**); `document_search` adds top-level **`citations`** — one more `Tool`,
routes/SSE/`CompletionEngine`/`LLMClient` **unchanged**. **Stage 5:** `@traced` emits **OTel
spans** + FastAPI root span → **Collector → Tempo**; Grafana dashboard + alerts as code; metrics
stay on Prometheus (traces only, ADR 0016). **Stage 6:** RAG eval via
`scripts/evaluate.py` (operator/CI, **not a boot hook**): **Tier 1** recall@k/MRR (offline-hash +
in-memory cosine) = **hermetic CI gate** vs `data/eval/baseline.json`; **Tier 2** judge
opt-in/billable, never CI (ADR 0017). **Stage 7:** `api` deploys to **K8s via Helm** (probes →
`/health`/`/ready`, verified on **`kind`**); **Terraform** (AWS) **validated, never applied**;
dev-vs-managed datastores gated by `devDependencies.enabled` (ADR 0018). **No auth** — stub (Stage 8).

Roadmap **`docs/PROJECT_STATUS.md`** (canonical). Detail `docs/stage-summaries/stage-0{1..7}.md`.
Rationale **`docs/adr/`** 0001-0018 (index in `docs/adr/README.md`); 0018 = K8s/Helm + Terraform.

## Layout

```
services/api/    app.py · routes/{health,meta,chat} · schemas.py
                 completions.py = CompletionEngine seam + OrchestratorEngine
services/agents/ base.py (Agent · ToolAgent) · tools.py (registry, 3 offline tools)
services/orchestrator/  base.py · graph.py (LangGraph) · llm.py (LLMClient) · conversations.py
services/retrieval/  embeddings.py (seam) · store.py (Qdrant) · ingest.py · retriever.py ·
                 tool.py (document_search — injection boundary)
services/monitoring/  tracing.py (build_tracer_provider seam · OTLP/Local providers) · base.py (SpanExporter=OTel's)
services/evaluation/  metrics · dataset · retrieval (RetrievalEvaluator · InMemoryCosineStore) · baseline · judge · services/security/  ⬜ stub
shared/  config · logging · observability (@traced) · datastores · migrations · version
data/corpus/  RAG corpus (scripts/ingest.py) · data/eval/  dataset.json + baseline.json (scripts/evaluate.py) · migrations/  NNNN_name.sql forward-only · tests/ mirrors source
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
- **Hermetic external-hop seams — read ADR 0009/0011/0016 first:** the `test` profile **cannot
  construct** `AnthropicClient`, `VoyageEmbeddingsClient`, *or* `OTLPTracerProvider` (each raises
  before its endpoint/key is read). Go via `build_llm_client`/`build_embeddings_client`/
  `build_tracer_provider`. Guard keys on the *profile*, not the key's absence. `test` gets a
  `LocalTracerProvider` (real spans, never leave process); tracing is **not** a `prod` boot req —
  unset `OTEL_EXPORTER_OTLP_ENDPOINT` runs untraced and logs it (ADR 0005/0016).
- **No sampling params.** Opus 4.7+ **rejects `temperature`/`top_p`/`top_k` (400)**; `budget_tokens`
  gone (use `thinking={"type":"adaptive"}`). `max_tokens` **required**; `ChatCompletionRequest.temperature` kept for wire compat, **not forwarded**.
- **Agent:** routes call the `CompletionEngine` on `app.state.engine`; `create_app(...,
  engine=...)` overrides it (stops the lifespan rebuilding it). `ToolRegistry.default()` = 3
  offline tools; `document_search` added via `with_tools` when Qdrant is up. `Tool.run` is
  **async**. `calculator` walks an AST allow-list, **never `eval`**. Failing tool → `is_error`.
- **Retrieval (ADR 0011-0014):** ingestion is an operator action (`scripts/ingest.py`), never
  a boot hook — **costs money** (Voyage) outside `test`. Qdrant point ids are UUIDv5 of the
  chunk id (server rejects arbitrary strings; re-ingest idempotent). **Retrieved text is
  untrusted** — `document_search` nonce-fences excerpts; citations are typed, never parsed from
  text. Delimiting ≠ immunity (Stage 8); widening the corpus changes the threat model.
- **Eval (ADR 0017):** RAG retrieval only. Tier 1 recall@k/MRR = **hermetic CI gate** vs `data/eval/baseline.json` (**never lower to pass**); Tier 2 judge billable, opt-in, never CI.
- **Persistence:** Postgres is source of truth, Redis only caches. Writes **invalidate**
  (Postgres first, then `DELETE`) — never rewrite. A Redis failure degrades to a Postgres
  read: the **one** sanctioned exception to fail-loud (ADR 0008). Migrations are plain SQL,
  forward-only, advisory-locked.
- **Logging:** `get_logger(__name__)`; log **events** not sentences —
  `_logger.info("http.request", extra={...})`. One JSON object/line. Never log
  PII/secrets/tokens (nor queries/excerpts — attacker-influenced). **Tracing (ADR 0016):**
  `@traced` on new app functions → OTel span carrying **only** `code.function`/`code.namespace`
  (read at decoration, never call data); error status = exception **type name** only. **Exempt:**
  `shared/{logging,observability}.py` (recursion), async generators/lifespan, LangGraph nodes.
- **Errors:** fail loud. Envelope `{"error": {type, message, request_id}}`; 500s never leak
  internals. Handlers on **Starlette's** `HTTPException`.
- **Types:** mypy `strict`, everything annotated incl. tests. Narrow with `isinstance` not
  `# type: ignore`. Depend on narrow Protocols not concrete drivers. **Deps:** `==` pins,
  `uv.lock` committed, `--frozen` installs.
- **Tests:** `tests/unit/`, mirror source. Test contracts, not internals. Write the failing
  test first; never edit a test to make it pass. Switching profiles needs
  `monkeypatch.setenv` **+** `get_settings.cache_clear()`. Opt-in layers skip by default:
  `TEST_DATABASE_URL`/`TEST_REDIS_URL`, `TEST_QDRANT_URL`, `RUN_LIVE_CONTRACT_TESTS=1` + keys.
- **Ending a stage:** `docs/contributing.md` → "Ending a stage" is **canonical** and lists **six**
  required updates — do all six before committing. **README is one** (it drifts silently).
- **Container boot is required, not deferrable.** Before self-report, `docker build` + run in
  **both** `prod` and `test` profiles and curl the endpoints — a green `pytest` does not prove
  the container boots (Stage 3 deferred it; CI caught two failures a 2-min local boot catches).
- **Architecture doc:** `docs/architecture.md` is source; `architecture.html` is **generated** by
  `scripts/build_architecture.py` — never edit it (a test fails on drift). Diagrams: pre-rendered
  **SVG** in `docs/diagrams/`, no CDN/JS (ADR 0010); rendering needs Node/`npx`, build + `--check`
  only Python. A Mermaid keyword (`graph`, `end`) as a node id fails the render.

## Run / test / lint

```bash
uv sync                                        # install
export ANTHROPIC_API_KEY=sk-ant-... ; export VOYAGE_API_KEY=pa-...   # real calls only; never dev/test
uv run uvicorn services.api.app:app --reload   # API only  -> :8000
docker compose up -d --build                   # full stack
uv run python scripts/ingest.py                # ingest corpus -> Qdrant (costs $ outside test)
uv run python scripts/evaluate.py              # Tier 1 RAG eval + CI regression gate (hermetic)
pwsh scripts/verify.ps1   # or ./scripts/verify.sh — the whole gate (ruff · format · mypy · pytest)
uv run python scripts/build_architecture.py   # regenerate architecture.html (needs npx for diagrams)
```

Endpoints: `/health` `/ready` `/version` `/metrics` `/docs` · `POST /v1/chat/completions`
(`"stream": true` SSE; optional `conversation_id`; `citations`). Grafana :3001 · Tempo :3200 · Collector :4317/:4318.

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
- **`.git/index.lock` strands = two git actors racing the index, not a code bug.** A recurring
  **0-byte** `index.lock` with **no `git.exe` alive** means a git process created the lock
  (`O_CREAT|O_EXCL`) but was hard-killed before writing+releasing it — normal error-exit removes
  it, only `TerminateProcess` strands it. Contributors on this machine: **(1)** the Claude Code
  **harness polls `git status` in the background** (index stat-refresh takes `index.lock`), and
  **(2)** a standalone **Git Bash window opened in the repo** (`--cd=…production-llm-platform`).
  When either races a CC `add`/`commit`, you get "Another git process seems to be running…
  index.lock: File exists". **Rule: one git actor at a time on this repo** — close or stay off
  that Git Bash window while CC commits; if CC's harness exposes a pause for background git
  polling, use it during commits. Recovery: with **no `git.exe` running**, deleting the 0-byte
  lock is safe. This is **separate from** the Stage-7 Defender issue (`tmp_obj_*` strand +
  "Operation not permitted" on unlink — that was on-access scanning, fixed by the folder
  exclusion above); both can surface together but have different causes.

## Deferred — do NOT build early

| Stage | Deferred |
|:--:|---|
| 8 | Auth, guardrails, rate limiting, RAG injection hardening — **API unauthenticated** |
| 9 | Load/chaos testing, SLOs, **OTel metrics export** (traces-only shipped, ADR 0016), pool tuning, circuit breaking, prompt caching, context compaction · **10** Portfolio polish |

## Known issues

- Starlette 1.3.1 deprecates `httpx` for `TestClient` (warning only). 422 stringifies Pydantic's
  raw error list into `message`. A datastore failing `connect` at boot stays `unavailable` until restart.
- **`/ready` does not check schema version** — a failed migration leaves the service
  reporting ready while every chat query fails (ADR 0007).
- **No per-conversation concurrency control** — two concurrent turns on one `conversation_id`
  collide on `(conversation_id, position)`; the second fails loudly, not serialised (ADR 0008).
- **Editing a doc shorter orphans its tail chunks** in Qdrant — ingestion is upsert-only (ADR 0012).
- **Hermetic suite can't catch a wrong belief about the real APIs** — run the opt-in live contract test (ADR 0015) when changing `llm.py`/`embeddings.py`.
