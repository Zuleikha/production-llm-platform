# Stage 4 — RAG — self-report

- **Stage:** 4 of 10 (RAG)
- **Date:** 2026-07-16
- **Version:** `0.1.0` (unchanged)
- **Status:** Build complete, full gate green, both container profiles booted,
  live contract test run. **Not committed** — awaiting independent manual
  verification, per the stage constraint.

This report states only what was directly run and observed this stage. It does
not restate prior-stage claims as re-verified.

---

## Task 7 clarification (requested)

An earlier status update of mine implied all of tasks 1–7 were done while the
live-Qdrant integration layer had in fact only ever *skipped* (it gates on
`TEST_QDRANT_URL`, which was unset). That was wrong to imply. It has since been
run for real:

- Started Qdrant via `docker compose up -d qdrant`, then
  `TEST_QDRANT_URL=http://localhost:6333 uv run pytest -k RealQdrant` →
  **4 passed** (full ingest→embed→store→retrieve→cite slice, idempotent
  re-ingest, point-id acceptance, and the new `get_collections()` probe).
- That run surfaced a client/server version skew warning (client 1.18.0 vs the
  compose-pinned server 1.12.4). I bumped the compose Qdrant image to
  `v1.18.1`, recreated the container, and re-ran → **4 passed, no skew
  warning**. Documented in ADR 0012.

So at the point of that earlier update the hermetic layers were green but the
live-Qdrant layer was unrun; it is now run and green.

---

## What was built, file by file

### New source modules

- **`services/retrieval/embeddings.py`** — the embeddings seam. `EmbeddingsClient`
  Protocol; `VoyageEmbeddingsClient` (real, batches at Voyage's 128-text limit,
  asymmetric document/query embedding, **refuses to construct under `test`**);
  `HashingEmbeddingsClient` (deterministic offline double producing genuine
  cosine similarity); `build_embeddings_client(settings)` factory. Mirrors
  Stage 3's `llm.py` (ADR 0011).
- **`services/retrieval/store.py`** — `QdrantVectorStore` on `qdrant-client`:
  `ensure_collection` (idempotent, never recreates), `upsert` (batched),
  `query`. `point_id_for` maps chunk ids to deterministic UUIDv5 (the Qdrant
  server rejects arbitrary string ids). ADR 0012.
- **`services/retrieval/ingest.py`** — `load_corpus` → `chunk_documents`
  (LlamaIndex `SentenceSplitter`) → embed → `upsert`. `SourceDocument`,
  `DocumentChunk`, `IngestionReport`. ADR 0011.
- **`services/retrieval/retriever.py`** — `VectorRetriever` (embed query, search,
  drop matches below a score floor) and `build_retriever(settings, client)`
  (returns `None` when there is no Qdrant, so the agent degrades to ungrounded
  rather than failing).
- **`services/retrieval/tool.py`** — `DocumentSearch`, the agent's retrieval
  tool and the prompt-injection boundary. Nonce-fenced excerpts, untrusted-data
  label, typed citations out-of-band. ADR 0014.
- **`scripts/ingest.py`** — the ingestion CLI (operator action; costs money
  outside `test`).

### Rewritten / widened

- **`services/retrieval/base.py`** — implemented the Stage 1 stub. Split into
  `DocumentChunk` (what you store) and `RetrievedDocument` (what you get back),
  correcting the Stage 1 sketch that had `upsert` take the result type.
  `Retriever.retrieve` `top_k` widened `int = 5` → `int | None = None`.
  `VectorStore` ABC gained `ensure_collection`; methods no longer raise.
- **`services/agents/tools.py`** — `Tool.run` is now **async** and returns a
  typed `ToolResult` (content + citations) instead of `str`. Added `Citation`,
  `ToolResult`, `ToolRegistry.with_tools`. The three offline tools updated;
  `ToolRegistry.default()` still returns exactly those three (no retrieval).
- **`services/orchestrator/graph.py`** — `_act`/`_invoke_one` await tools and
  accumulate citations into a new reduced `citations` state channel
  (deduplicated by chunk id). `AgentState`, `_as_state`, `_initial` updated.
- **`services/orchestrator/base.py`** — `AgentResult.citations`; threaded
  through `answer` and `answer_stream`.
- **`services/api/completions.py`** — `Completion.citations`; threaded through
  `complete`/`stream`.
- **`services/api/schemas.py`** — new `CitationModel`; new top-level `citations`
  field on `ChatCompletionResponse` (list) and `ChatCompletionChunk`
  (`list | None`, final frame only). ADR 0013.
- **`services/api/routes/chat.py`** — `_citations()` render helper; citations on
  the whole response and on the final SSE frame only.
- **`services/api/app.py`** — `_build_tools()` composes the retrieval tool onto
  the registry when Qdrant is live; wired into `_build_engine`.
- **`shared/config.py`** — Voyage + retrieval settings; extended the prod
  boot-refusal validator to require `VOYAGE_API_KEY`; added a chunk-overlap <
  chunk-size validator.
- **`shared/datastores.py`** — `QdrantDatastore` swapped from raw-`httpx`
  `/readyz` to `qdrant-client` (`get_collections()` probe); registry exposes a
  `qdrant_client` property.
- **`services/retrieval/__init__.py`** — exports the new public surface.

### Corpus, tests, config, infra

- **`data/corpus/{deployments,incident-response,observability,code-review}.md`** —
  domain-agnostic generic-engineering fixture corpus. **`data/README.md`** —
  corpus notes, kept one level *above* `data/corpus/` so it is not itself
  ingested.
- **New tests:** `test_embeddings.py`, `test_ingest.py`, `test_retrieval_tool.py`,
  `test_integration_retrieval.py`. **Updated:** `test_tools.py`, `test_graph.py`,
  `test_config.py`, `tests/fakes.py` (added `StubRetriever`, `InMemoryVectorStore`).
- **`pyproject.toml`** — promoted retrieval deps to base; added a scoped mypy
  override for `voyageai`. **`.env.example`**, **`docker-compose.yml`**,
  **`Dockerfile`** — Voyage key, Qdrant image bump, corpus + ingest CLI in the
  image.
- **ADRs 0011–0015**; **`docs/adr/README.md`** index; **`docs/architecture.md`**
  + regenerated **`architecture.html`** + two re-rendered SVGs (two stale ones
  removed); **`docs/PROJECT_STATUS.md`**; **`CLAUDE.md`**;
  **`services/agents/README.md`**, **`services/retrieval/README.md`**.

---

## Test count before / after

Command: `uv run pytest` (from a clean run).

- **Before (Stage 3 baseline):** `185 passed, 3 skipped`
- **After:** `246 passed, 9 skipped`

The 3 pre-existing skips are the live-Postgres/Redis integration tests. The 6 new
skips are the 4 live-Qdrant tests (`TEST_QDRANT_URL`) and the 2 live-provider
contract tests (`RUN_LIVE_CONTRACT_TESTS=1` + keys) — all opt-in. Every skip is
by design; the default suite is hermetic and needs no network and no keys. (The
4 live-Qdrant and 2 live-provider tests were additionally run for real this
session against a live Qdrant / the live APIs — see below.)

---

## Lint / format / type-check — exact commands and results

Run from a clean tree after all changes:

| Command | Result |
|---|---|
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `64 files already formatted` |
| `uv run mypy` | `Success: no issues found in 63 source files` |
| `uv run pytest` | `246 passed, 9 skipped, 1 warning` |
| `uv run python scripts/build_architecture.py --check` | `architecture.html is up to date.` |

The one warning is the pre-existing Starlette `TestClient`/`httpx` deprecation,
unrelated to this stage.

---

## CLAUDE.md line count before / after

- **Before:** 149 lines
- **After:** 149 lines

A whole subsystem (RAG) of new facts was added; the file was kept at the limit by
tightening prose across the conventions, run, and known-issues sections. It went
through 169 → 156 → 149 during editing; the final file is 149 lines (verified
`wc -l < CLAUDE.md`).

---

## Package versions pinned for the retrieval promotion

Promoted from the `retrieval` optional-dependency group to **base** dependencies,
exact `==` pins per ADR 0001:

| Package | Version |
|---|---|
| `llama-index-core` | `0.14.23` |
| `voyageai` | `0.5.0` |
| `qdrant-client` | `1.18.0` |

**Deliberate deviation from the literal prompt:** I pinned **`llama-index-core`,
not the `llama-index` meta-package.** The meta-package depends on
`llama-index-llms-openai` and `llama-index-embeddings-openai`, which would install
the **OpenAI SDK** into the base image of an Anthropic + Voyage platform. Only
`Document` and `SentenceSplitter` are used, and both live in core. This is the
same call, on the same grounds, as dropping `langchain` in Stage 3 (ADR 0006).
Documented in ADR 0011 and in `pyproject.toml`. Verified no OpenAI SDK is present:
`uv pip list | grep -i openai` → nothing.

**One transitive note, disclosed rather than hidden:** `voyageai` pulls
`langchain-text-splitters` transitively (it uses it internally for its own
chunking helper, which this project does not call). `langchain-core` and
`langchain-protocol` were already in `uv.lock` before this stage via `langgraph`;
`langchain-text-splitters` is the one genuinely new `langchain-*` package, and it
arrives through Voyage, not through a direct dependency.

`[tool.uv] link-mode = "copy"` retained. `uv.lock` regenerated and verified in
sync (`uv lock --check` → resolved, exit 0), so the `--frozen` Docker build works.

---

## Voyage AI model chosen and why

**`voyage-3.5-lite`, 1024 dimensions** (ADR 0011). The small/cheap tier of the
current Voyage generation — more than adequate retrieval quality for a
demonstration corpus, at minimal per-token cost. Both the model and the
dimensionality are settings (`VOYAGE_MODEL`, `VOYAGE_EMBEDDING_DIMENSIONS`), so a
larger tier is a one-line change. Anthropic ships no embeddings API; Voyage is its
documented RAG pairing, so a second vendor is unavoidable and is made a
first-class guarded dependency.

---

## Qdrant collection/schema design and why

ADR 0012. One collection (`documents`, configurable), **cosine** distance
(embeddings are normalised; direction is what matters), vector size taken from the
embeddings client (not a second setting that could drift). Payload carries
`chunk_id`, `text`, `document_id`, `source`, `position` — enough to rebuild a
citation with no second lookup. **Point ids are deterministic UUIDv5** of the
chunk id: the Qdrant *server* rejects arbitrary string ids (the client type hint
`int | str | UUID` misleads), and determinism makes re-ingest idempotent.
`ensure_collection` never recreates an existing collection (that would delete its
vectors).

---

## Citation shape and why

ADR 0013. A **new top-level `citations` field** on the response — not folded into
`message.content` or `usage`. Each citation is `{id, document_id, source, score,
text}`. `id` is the exact chunk (`deployments.md:1`); `document_id` the document
(`deployments.md`). An ungrounded answer reports `"citations": []`, not a missing
field. Streaming carries them on the **final frame only** (an agent can retrieve
after its first answer text, so they can't be accumulated). Provenance is carried
out-of-band as typed `Citation` data, never parsed from the excerpt text — so a
document cannot forge its own citation (a test pins this).

---

## Prompt-injection mitigation applied (mechanism, not claim)

ADR 0014; documented in `services/retrieval/tool.py`, `services/agents/README.md`,
and `data/README.md`. Retrieval is the first tool whose result is not a pure
function of its arguments — it returns document text, which can be
attacker-influenced. Mechanism:

1. **Per-call nonce fence.** Each excerpt is wrapped in
   `<excerpt-{nonce}>…</excerpt-{nonce}>` where `nonce = secrets.token_hex(8)`,
   generated fresh **every call**. A document author cannot predict it, so cannot
   write text that closes the fence and appears to escape into instruction
   context. Tests pin that a stale/guessed closing marker inside a document is
   inert (does not close the real fence) and that the nonce differs per call.
2. **Trust label travels with the data** (a preamble in the tool result, not the
   system prompt), so it cannot be evicted from a long context while the
   untrusted text remains.
3. **Provenance out-of-band** as typed citations — a document cannot forge its
   own citation.

**Explicitly NOT done, and why (scoped, not silent):** no classifier, no
instruction-pattern stripping, no trust tiers, no answer egress filtering.
Delimiting removes *ambiguity about what is data*; it is **not immunity** — a
persuasive injected instruction inside the fence may still be obeyed. The corpus
today is a committed, reviewed set of files (source-code trust level), so the
mitigation is proportionate. Full hardening is Stage 8. Widening what can enter
the corpus changes the threat model and must revisit ADR 0014 — stated at each
extension point.

---

## How test-profile Voyage mocking is enforced (mechanism, not claim)

Identical construction to the Anthropic guard (ADR 0009), keyed on the **profile**,
not the key's presence. Two independent mechanisms:

1. `build_embeddings_client(settings)` returns `HashingEmbeddingsClient` under
   `test` and never reaches the real constructor.
2. `VoyageEmbeddingsClient.__init__` **raises `RuntimeError` under `test`, before
   the key is read** — so no import order, fixture order, or monkeypatch turns a
   unit test into a paid call, including code that reaches past the factory.

`tests/unit/test_embeddings.py::TestTestProfileCannotCallVoyage` pins the
mechanism, including the distinguishing case: a real `VOYAGE_API_KEY` exported in
the OS environment does **not** change either behaviour (the test sets it via
`monkeypatch.setenv` and asserts the client still refuses and the factory still
returns the double). The double produces genuine cosine similarity (hashed
L2-normalised bag-of-words), so retrieval tests prove retrieval *worked* rather
than that a mock returned a fixture.

---

## Architecture doc updated and regenerated

`docs/architecture.md` (the source of truth) was updated to reflect Stage 4:
header/intro, the component-map diagram (retrieval stack, embeddings, Voyage,
Qdrant now holding data, `document_search` in the tool registry), the chat
sequence diagram (a grounded turn: `document_search` → Voyage+Qdrant → citations),
a new "Retrieval, grounding and citations" section, the config/quality-gate rows,
the Planned table (retrieval removed), non-goals, the seams and fail-loud
properties, and the ADR see-also list.

Regenerated with the exact command:

```
uv run python scripts/build_architecture.py
```

This re-rendered the two changed Mermaid diagrams to inline SVG under
`docs/diagrams/` (via `npx`, which is available here) and removed the two now-unused
SVGs. `uv run python scripts/build_architecture.py --check` →
`architecture.html is up to date.`, and `tests/unit/test_architecture.py` → 14
passed. `architecture.html` was **not** hand-edited. Diagrams remain pre-rendered
static SVG with no CDN/JS (ADR 0010); no Mermaid keyword used as a node id.

---

## Container built and booted in both profiles — exact commands and output

Docker daemon confirmed running (`docker ps`, per the CLAUDE.md quirk that
`docker compose version` is not a real check). Datastores were brought up with
`docker compose up -d postgres redis qdrant`.

**Build:**
```
docker build -t plp/api:stage04 .
```
→ built successfully (multi-stage, `--frozen` install from `uv.lock`, non-root).

**test profile:**
```
docker run -d --name plp-test -p 8010:8000 -e ENVIRONMENT=test plp/api:stage04
```
Healthy in 7s. Curl output:
```
/health   → {"status":"ok","service":"api","version":"0.1.0","environment":"test"}
/ready    → {"status":"ready","checks":{"postgres":"not_configured","redis":"not_configured","qdrant":"not_configured"}}
/version  → {"service":"api","version":"0.1.0","environment":"test"}
POST /v1/chat/completions {"messages":[{"role":"user","content":"hello stage 4"}]}
  → {... "message":{"role":"assistant","content":"You said: hello stage 4"},
       "finish_reason":"stop"}, "usage":{"prompt_tokens":3,"completion_tokens":5,"total_tokens":8},
       "citations":[]}
```
The new top-level `citations` field ships (empty — test profile wires no
retrieval, and the scripted model does not call the tool).

**prod profile** (real datastores on the compose network; placeholder API keys —
they satisfy the presence-only boot validator and let me verify wiring with **no
paid call**; I did not curl chat here because a real retrieval-augmented chat in
prod would hit paid Anthropic + Voyage):
```
docker run -d --name plp-prod --network production-llm-platform_platform \
  -e ENVIRONMENT=prod \
  -e DATABASE_URL=postgresql://platform:platform@postgres:5432/platform \
  -e REDIS_URL=redis://redis:6379/0 -e QDRANT_URL=http://qdrant:6333 \
  -e ANTHROPIC_API_KEY=placeholder-prod-boot-not-real \
  -e VOYAGE_API_KEY=placeholder-prod-boot-not-real \
  -p 8011:8000 plp/api:stage04
```
Healthy in 6s. Curl output:
```
/health   → {"status":"ok",...,"environment":"prod"}
/ready    → {"status":"ready","checks":{"postgres":"ok","redis":"ok","qdrant":"ok"}}
/version  → {"service":"api","version":"0.1.0","environment":"prod"}
```
All three stores `ok` — Qdrant probed via the new `get_collections()` (logs show
`GET http://qdrant:6333/collections "HTTP/1.1 200 OK"`). Boot logs show the
serving engine wired the retrieval tool:
`retrieval.tool_enabled {"collection":"documents"}` and
`embeddings.client_selected {"client":"voyage","model":"voyage-3.5-lite"}`, after
`datastore.connected qdrant`.

**Observation worth recording** (the kind of thing the container-boot convention
exists to surface): the logs show the engine is built twice — once at module load
(`retrieval.tool_disabled`, because the datastore registry isn't connected yet)
and once in the lifespan after `startup()` (`retrieval.tool_enabled`). The
**serving** engine is the second one, so retrieval is wired on the request path.
This is the same two-phase rebuild the Stage 3 persistence code already relies on;
it is correct, not a bug, but it is only visible from an actual boot.

**In-container ingestion CLI** (proves the CLI works from the shipped image,
hermetically — test-profile override uses the offline hash, **no paid call**):
```
docker exec -e ENVIRONMENT=test -e QDRANT_URL=http://qdrant:6333 plp-prod \
  python scripts/ingest.py
  → ingested 4 chunks from 4 documents into 'documents' (profile=test, model=voyage-3.5-lite)
```
Qdrant collection afterwards: `status: green | points: 4 | vector size: 1024 |
distance: Cosine` — exactly the ADR 0012 design. Postgres schema present
(`conversations`, `conversation_messages`, `schema_migrations`).

Both containers and the scratch `documents` collection were torn down afterwards.

---

## Live contract test — exact commands, actual output, cost

Run this session with explicit human confirmation (double-gated:
`RUN_LIVE_CONTRACT_TESTS=1` **and** both keys). Keys were sourced from the
git-ignored `.env` for the single command so they never entered a tool argument
or the transcript.

```
set -a; . ./.env; set +a
RUN_LIVE_CONTRACT_TESTS=1 uv run pytest tests/unit/test_integration_retrieval.py \
  -k LiveProviderContract -s
```

Result: **2 passed, 10 deselected**. Actual observed output:

- **Anthropic** (`claude-opus-4-8`): `HTTP/1.1 200 OK`; captured line
  `[live anthropic] text='Paris' stop_reason=end_turn usage=in:28/out:5`. The
  fields the agent loop and usage accounting depend on are all present and
  correctly named (text deltas arrived, exactly one `TurnCompleted`, positive
  input/output tokens, replayable `raw_content`).
- **Voyage** (`voyage-3.5-lite`): two `200 OK` responses (document batch of 2,
  then the query); captured line
  `[live voyage] model=voyage-3.5-lite dims=1024 cos(related)=0.6131 cos(unrelated)=0.3515`.
  Vectors are **exactly 1024-dim** (the number baked into the Qdrant collection —
  the assertion that can only be checked live), one per input in order, and the
  query embeds closer to the related passage than the unrelated one (confirming
  the document/query asymmetry). Reported token usage: 14 (documents) + 7 (query).

**Approximate cost:** Anthropic 28 input / 5 output tokens at Opus rates
(≈ $0.0002); Voyage 21 tokens on `voyage-3.5-lite` (≈ negligible, sub-cent).
**Total well under US$0.001** — smaller than the pre-run estimate and than
Stage 3's ~$0.021.

---

## Deviations from the stage prompt

1. **`llama-index-core` instead of the `llama-index` meta-package.** See the
   package-versions section above and ADR 0011 — the meta-package would install
   the OpenAI SDK into the base image. The prompt asked for "LlamaIndex
   ingestion components", which core provides; this is a scoped substitution, not
   a reduction.
2. **Qdrant compose image bumped `v1.12.4` → `v1.18.1`** to match the pinned
   client's minor version (the client warns and may misbehave beyond a one-minor
   gap). Not in the prompt, but required for the client swap to be sound; ADR 0012.
3. **PROJECT_STATUS Stage 4 verification cell** was written as "pending
   independent verification" at self-report time, then updated to link
   `verification-log/stage-04-rag.md` once that independent verification passed
   and the log was written — matching how stages 1–3 are recorded. I did not
   fabricate the log; it was authored by the separate verification step.

No scope item was skipped, simplified, or silently deferred.

---

## Known limitations, risks, and deliberately deferred items

- **Editing a document shorter orphans its tail chunks in Qdrant.** Ingestion is
  upsert-only with per-position ids, so a document dropping from 5 chunks to 3
  leaves `:3`/`:4` behind. Fine for an append-mostly corpus; a per-document
  purge-before-write is the fix when it matters (ADR 0012).
- **Prompt-injection mitigation is delimiting + labelling, not immunity.** No
  classifier, trust tiers, or egress filtering. A persuasive injected instruction
  inside the fence may still be obeyed. Proportionate to a committed, reviewed
  corpus; hardening is Stage 8 (ADR 0014).
- **The `HashingEmbeddingsClient` double does not reproduce Voyage's
  document/query asymmetry** — it embeds both identically (a hash can't fake a
  trained model's asymmetry honestly). That asymmetry is therefore exercised
  *only* by the live contract test, which confirmed it (cos related > unrelated).
- **A live retrieval-augmented chat was not curled through a running container** —
  it requires paid Anthropic + Voyage calls. That exact path (agent →
  `document_search` → retrieve → cite → response carries citations) is instead
  proven by two tests that exercise the real route→graph→orchestrator→engine→wire
  stack: `TestCitationsReachTheClient` (hermetic) and
  `TestAgainstRealQdrant::test_the_full_slice_ingests_stores_retrieves_and_cites`
  (real Qdrant). The container boot proves the tool is wired and Qdrant reachable.
- **Retrieval is intentionally minimal:** no reranking, hybrid search, query
  expansion, or automatic re-ingestion. Not in scope for Stage 4.
- **The live contract test is opt-in and human-run**, not a CI guard — the correct
  trade for a billable test (ADR 0015). CI remains hermetic and key-free.
- **Left running for your verification:** the compose datastores
  (postgres/redis/qdrant) are still up. Nothing else from this session is running.

---

## Not done (per instruction)

No commit, no push. Stopping after this self-report. The commit is a separate step
after independent manual verification passes.
