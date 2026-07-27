# Stage 10 — Portfolio (self-report)

**Status:** complete, pending independent verification. No new platform capability —
this stage closes the open housekeeping items and packages the build for a hiring
reviewer (docs, a scripted demo, a case study, styled HTML renders). Every number
below was directly run and observed this session against the real stack; prior-stage
claims are cited, not re-verified.

---

## 1. Housekeeping fixes

### 1a. ADR 0020 index row — added
`docs/adr/README.md` gained the missing row (format matched exactly, stage 9):

```
| [0020](0020-reliability-load-chaos-resilience-slos.md) | Reliability: circuit breaker, prompt caching, context windowing, pool tuning, load & chaos testing, the OTel metrics pipeline, and SLOs | Accepted | 9 |
```

### 1b. `pyyaml` promoted to an explicit dependency
Added `pyyaml==6.0.3` (the currently-locked version) to `pyproject.toml`, re-locked,
and confirmed nothing else moved:

```
$ uv lock          → Resolved 141 packages in 3.94s
$ diff (old vs new uv.lock)  → only two added lines, both pyyaml:
>     { name = "pyyaml" },
>     { name = "pyyaml", specifier = "==6.0.3" },
$ uv sync --frozen → Checked 139 packages
$ uv run python -c "import yaml; print(yaml.__version__)" → 6.0.3
```

### 1c. Locust harness fixed — see §3.

---

## 2. `.env` key and architecture-history regeneration (operator actions)

- **The malformed `ANTHROPIC_API_KEY` was initially NOT fixed** — it read
  `ssk-ant-…` at stage start (verified by shape only, never logged), so the demo was
  first captured entirely against the **`test`-profile `ScriptedLLMClient`** (echo).
  **The operator then fixed and verified the key mid-stage**, so the model-dependent
  steps were **re-captured for real** against a `dev`-profile stack (real
  `claude-opus-4-8`): a real authenticated chat returned a substantive answer (85
  completion tokens, 1104 prompt tokens) and a real SSE stream returned actual model
  tokens — both recorded in the **dev-profile addendum** of `docs/demo.md`. **No demo
  step fabricates a call**: the echo captures are labelled test-profile, the real
  captures dev-profile. After the operator **authorised the billable ingestion**, the
  corpus was ingested (real Voyage, 4 chunks) and the **non-empty `citations` list was
  also captured for real** — a grounded call made the model call `document_search` and
  return a 4-entry typed `citations` array grounded in `incident-response.md` (dev
  addendum). So all six demo items now have real captures except the circuit breaker,
  which keeps its sanctioned hermetic fallback.
- **The `docs/architecture-history/generate.py` re-run** was not done by the operator
  either. I ran it as the local, gitignored close-out step; it renders every stage's
  diagrams through Node and is slow, so it was still running in the background at
  write time. It heals Stage 9's snapshot to its committed git blob and snapshots
  Stage 10 from the working tree (self-heals to the commit on the next run, as
  documented). Nothing under `docs/architecture-history/` was `git add`ed.

---

## 3. Locust harness fix and the pool-default decision

**The gap** (Stage 9): every user authenticated as one `test-principal` → one
rate-limit bucket → near-instant 429s, and no `conversation_id`, so Postgres/Qdrant
pools were never exercised. **The fix** (`tests/load/locustfile.py`, recorded as ADR
0020 **addendum 3**):

- **20 distinct principals** (`loaduser-0…19`), hashes committed in
  `config/environments/test.env` and mirrored into `docker-compose.test.yml` (drift
  guard `tests/unit/test_load_profile_compose.py` extended and passing).
- **A stateful subset** (~50% of `ChatUser`s carry a stable `conversation_id`).
- **A document-search-phrased subset** (~25% of prompts).

**Deviation, recorded:** the prompt said to mint keys with
`scripts/generate_api_key.py`. Its random `token_urlsafe(32)` keys, committed to
`test.env`/the locustfile, would **trip the gitleaks gate** (allowlist keys off the
`test-raw-key-`/`not-a-real` convention, not paths). So keys were minted with the
**same primitive** the script wraps — `services.security.auth.hash_key`, test pepper —
over deterministic convention names (`test-raw-key-loaduser-{i}`). Gitleaks-safe,
same minting function.

**Cost-free ceiling, stated:** under Mode 1 the scripted client never emits a
`tool_use` block, so the doc-search prompts do **not** reach Qdrant's *query* pool
(only `/ready`'s `get_collections` traffic does). Real query-pool load needs Mode 2
(a real model). Making the scripted client tool-call on a phrase would be new
orchestrator logic — out of scope.

**Real run** (Mode 1, `test` stack, 60 users = 30 `ChatUser` + 30 `ProbeUser`,
spawn-rate 10/s, 2 min):

| Type | reqs | failures | latency |
|------|-----:|----------|---------|
| chat | 1580 | 353 (22.3%) — all `429` | p50 16 ms · p95 90 ms · p99 140 ms · max 602 ms |
| chat-stream | 155 | 28 (18%) — all `429` | p50 43 ms · p95 110 ms |
| /health | 2656 | 0 | p50 3 ms · p99 21 ms |
| /ready | 863 | 0 | p50 6 ms · p99 60 ms |

Server-side during the run: **0 ERROR-level lines, 0 pool timeouts, 0 `500`s, 0
`ratelimit.degraded`** (Redis stayed up); Postgres held **18 conversations / 1584
messages** (the stateful path is genuinely exercised now — the old harness: zero).
The 22% chat-429 rate (vs the ~100% a single shared bucket would give) is the
per-principal limiter working across 20 buckets.

**Decision: pool defaults CONFIRMED, not changed.** With the pools genuinely under
load, nothing queued, starved or timed out and the chat path held a sub-150 ms p99,
so there is no bottleneck to justify raising `db_pool_max_size` /
`redis_pool_max_connections` / `qdrant_pool_max_connections` (all 10). Revisit if
Mode 2 (real per-request latency holds pool connections longer) or a higher
concurrency target shows queuing.

---

## 4. Demo walkthrough

**Location:** `scripts/demo.sh` (the repeatable artifact — chosen over a prose list
so a reviewer can *reproduce* it) with its real captured transcript and narration in
`docs/demo.md` (source of truth) → rendered to `demo.html`.

**Capture note:** taken from a container on the compose network (`http://api:8000`),
not `localhost:8000`, because Docker Desktop's host port-forward on this machine
intermittently returns empty replies (exit 52) while the container serves 200s
internally — a documented environment quirk, not a code fault. Same real stack, real
HTTP. All output is from the `test` profile (scripted echo model).

Real captured output:

1. **Authenticated chat →** `HTTP 200`, OpenAI-shaped `choices`/`usage` + top-level
   `"citations":[]`. Body echoed `"You said: …"`.
2. **Streaming →** `text/event-stream`: a role-priming frame, one content delta per
   token, a final frame with `finish_reason`+`citations`, then `data: [DONE]`.
3. **document_search / citations →** cost-free ceiling `"citations":[]` (scripted
   client never tool-calls). Then, after the key fix **and** authorised corpus
   ingestion, a real dev-profile grounded call returned a **non-empty 4-entry typed
   `citations` array** (`incident-response.md` 0.65 → `code-review.md` 0.35), the
   model grounding its answer in the top hit — `HTTP 200`, usage 5110/587 tokens
   (dev addendum in `docs/demo.md`).
4. **Unauthenticated →** `HTTP 401`, uniform envelope
   `{"error":{"type":"http_error","message":"Missing or invalid credentials.","request_id":"…"}}`.
5. **Rate limiting →** 70 requests on one principal: **60 × `200` then 10 × `429`** —
   the 60/60s per-principal limiter cutting in exactly at threshold.
6. **Circuit breaker →** fallback to the hermetic fault-injection tests (no cost-free
   live fault possible; no Anthropic base-URL override to a dead endpoint this
   session): `uv run pytest tests/unit/test_resilience.py tests/unit/test_llm.py -q`
   → **36 passed**. Same fallback Stage 9's prompt allowed.

**Real dev-profile re-captures (after the key fix):** steps 1 and 2 were re-run
against a real `claude-opus-4-8` and produced genuine model content (a real readiness-
probe answer; a real streamed "Circuit breaker, retry with backoff, bulkhead") — both
in the `docs/demo.md` dev-profile addendum. Steps 4–6 are auth/limiter/breaker
behaviour, unaffected by which model runs, so they were not re-captured.

---

## 5. Case-study writeup

**Location:** `docs/case-study.md` (source) → rendered to `case-study.html`.

**One-paragraph summary:** a narrative for a hiring reviewer told through the ADR
trail — the platform as a demonstration of the *engineering around* an LLM call
(stable seams filled stage by stage, with a stage→concern→seam→evidence table); the
handful of decisions most worth attention (LangChain/llama-index-meta rejected for
the same dependency reason; the hermetic-seam pattern reused across
Anthropic/Voyage/OTel; the deliberately fail-open rate limiter; validate-never-apply
Terraform; retrieved text fenced as untrusted); an honest "what is deliberately not
built" section drawn from `PROJECT_STATUS.md`'s deferred lists; and the known open
issues carried into the final state — including the `request_id: null` on the
Postgres-down 500, restated as accepted/deferred, not dropped.

---

## 6. Shared HTML doc-renderer (Scope §5)

Factored `build_architecture.py`'s markdown-to-HTML machinery (the CSS block, the
`markdown-it` render + table-scroll rules, the page shell, the build-stamp recovery)
into **`scripts/doc_render.py`** — one styling source. `build_architecture.py` keeps
all Mermaid-specific logic (diagram hashing, `docs/diagrams/`, `--render`) and now
calls into the shared module.

**A new `scripts/build_docs.py`** drives `docs/case-study.md → case-study.html` and
`docs/demo.md → demo.html` through the shared renderer. **Chosen over extending
`build_architecture.py`** because these prose pages carry no Mermaid diagrams, so
they need none of its diagram/Node machinery; a separate pure-Python script keeps
each script's contract single-purpose and its `--check` toolchain-free. No CDN, no JS
(ADR 0010).

```
$ uv run python scripts/build_docs.py         → Wrote case-study.html / demo.html
$ uv run python scripts/build_docs.py --check → case-study.html and demo.html are up to date.
```

**New drift-guard test** `tests/unit/test_docs_render.py` (10 tests, mirroring
`test_architecture.py`): existence, `--check` drift, generated-marker, no-external-
resource, no-JS, and a single-styling-source assertion (the CSS lives only in
`doc_render.py`, never copied into a build script). All passing.

---

## 7. Architecture doc / diagram final pass (Scope §6)

`docs/architecture.md` checked end-to-end and corrected where it had gone stale:

- Intro banner `Stage 5 of 10` → `Stage 10 of 10 — feature-complete`, with a new
  paragraph covering Stages 6–9.
- **Fixed a real drift:** the "metrics are not exported through the OTel collector …
  deferred to Stage 9" paragraph (true at Stage 5/8) now states that Stage 9 *built*
  the spanmetrics/servicegraph pipeline that drives Tempo's service map — the exact
  kind of dangling inconsistency this stage exists to remove.
- "Planned — not yet implemented" (which listed Stage 10 as "Not started") → "Planned
  — nothing left to build."

**One new summary diagram added** (Scope §6 invited one if the set lacked a single
whole-system view): a **request-lifecycle** flowchart showing every cross-cutting
production gate a chat request passes — auth → rate-limit → input guardrail → circuit
breaker — with each gate's status code (401/429/400/503). The existing component map
is the *topology*; this is the one view of the *request path through all the later
stages' concerns*, which the topology omitted.

Regenerated and drift-checked:

```
$ uv run python scripts/build_architecture.py           → Wrote architecture.html (8 diagrams)
$ uv run python scripts/build_architecture.py --check    → architecture.html is up to date.
```

---

## 8. Container boot — both profiles (standing rule)

Both booted this session against the real Compose stack and curled (via the compose
network, per the host-proxy quirk above):

- **`test` profile** (`docker-compose.yml` + `docker-compose.test.yml`): up, healthy,
  20 load principals present; drove the full demo + Locust run.
- **`prod` profile** (`ENVIRONMENT=prod`, force-recreated `api`): boots — the prod
  settings validator passed with all required keys present; `/health` → `200`
  `{environment:prod}`, `/ready` → `200` `{postgres:ok, redis:ok, qdrant:ok}`, and an
  unauthenticated chat → `401` (auth wired, no model call). Stack then restored to the
  `test`-profile Mode 1 state.

---

## 9. Quality gate

`./scripts/verify.sh` — **all checks passed**:

- ruff lint: **All checks passed**
- ruff format: clean (88 files)
- mypy `strict`: pass (incl. the two new scripts + the shared module)
- pytest: **405 passed, 9 skipped** — **+10 over the Stage 9 baseline of 395**, all
  ten new tests being `test_docs_render.py`. No prior-stage test was modified except
  `test_security.py::test_build_auth_provider_uses_the_test_profile_key`, whose
  `principal_count == 1` was an incidental fixture detail invalidated by the 20
  legitimately-added load principals; it now asserts the real contract
  (`== len(parse_key_store(settings.api_keys))`), not a magic number.
- gitleaks: **no leaks found** (the committed convention keys are allowlisted; a real
  key would still trip).
- pip-audit: **No known vulnerabilities found**.

---

## 10. Close-out files (five by CC; the sixth is the verifier's)

1. ✅ `docs/stage-summaries/stage-10-portfolio.md` — this file.
2. ✅ `docs/PROJECT_STATUS.md` — Stage 10 complete, 10/10, no "next milestone" row.
3. ✅ `CLAUDE.md` — **150 → 149 lines** (under 150; before/after stated per the rule).
4. ✅ `docs/architecture.md` + regenerated `architecture.html` (+ `case-study.html`,
   `demo.html` via `build_docs.py`).
5. ✅ `README.md` — banner "Stage 10 of 10 complete", roadmap row filled, stack/repo
   blocks and docs table updated (demo.md, case-study.md, demo.sh, build_docs.py).
6. ⏳ `docs/verification-log/stage-10-portfolio.md` — **intentionally not written by
   CC**; it is authored after independent manual verification passes.

---

## 11. Deviations from scope

- **Locust keys** minted via the `hash_key` primitive over gitleaks-safe convention
  names rather than `generate_api_key.py`'s random keys — see §3 (gitleaks would trip
  on a committed random key).
- **Demo output captured via the compose network**, not `localhost` — Docker Desktop
  host-proxy quirk (§4); documented in `CLAUDE.md` quirks.
- **`document_search` demo ceiling (resolved mid-stage).** The cost-free scripted
  client cannot tool-call, so the initial captures were echo-only. The operator then
  fixed the `.env` key and authorised the billable corpus ingestion, so real
  dev-profile captures were added for the chat, streaming **and** grounded/citations
  steps (dev addendum). The **Locust** ceiling still stands (Mode 1 stays scripted,
  so Qdrant's query pool is only exercised under Mode 2) — that was not re-run.
- **One prior-stage test assertion updated** (`test_security.py`) — to the real
  contract, not to force a pass (§9).

## 12. Known limitations carried into the final state (restated, not buried)

- **`request_id: null` on the Postgres-down 500** (chaos scenario 1): pre-existing,
  accepted and deferred — now recorded in `docs/case-study.md`, `PROJECT_STATUS.md`
  and `CLAUDE.md`.
- Also still open by decision (unchanged this stage): per-conversation concurrency
  control, Voyage circuit breaking, `/ready` not checking schema version, and the
  other items in `PROJECT_STATUS.md`'s deferred lists.
