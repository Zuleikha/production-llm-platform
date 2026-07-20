# Stage 06 — MLOps: self-report

**Status:** built, tested and self-verified this session. **Not committed** (per the
stage brief — commit follows independent manual verification).

Everything below is what was directly run and observed this session. Prior-stage
claims are not re-verified here.

---

## 1. What was implemented, file by file

### Evaluation service (`services/evaluation/`)

| File | What it is |
|------|-----------|
| `base.py` | The `Evaluator` ABC (contract kept as-is) plus the result types `EvalCase`, `CaseResult`, `RetrievalReport`. Was the Stage 1 stub that raised. |
| `metrics.py` | Pure, hand-verifiable functions: `recall_at_k`, `reciprocal_rank`, `mean`. No I/O — the arithmetic the gate rests on, checkable on paper. |
| `dataset.py` | Loads and strictly validates `data/eval/dataset.json` into `EvalCase`s (`load_eval_cases`, `parse_case`). Fails loud on a malformed case. |
| `retrieval.py` | `RetrievalEvaluator` (grades any `Retriever`, document-level recall@k/MRR), `InMemoryCosineStore` (a real brute-force cosine `VectorStore`), and `build_offline_retriever` (the hermetic Tier 1 pipeline: corpus → offline-hash embeddings → in-memory cosine). |
| `baseline.py` | Loads `data/eval/baseline.json` (`load_baseline`) and runs the regression gate (`check_regression`, `RegressionResult`). |
| `judge.py` | Tier 2: `LLMJudge` (faithfulness + citation-accuracy scoring) and `generate_grounded_answer`, both over the narrow `LLMClient` protocol; `parse_verdict` / `build_prompt` are pure and hermetically tested. |
| `__init__.py` | Re-exports the public surface. |
| `README.md` | Rewritten from "planned" to reflect what is built (two-tier table, layout, run commands). |

### Data fixtures (`data/eval/`)

- `dataset.json` — 8 graded queries (2 per corpus document), each with expected
  `document_id`(s) and a `note` explaining the choice.
- `baseline.json` — last-accepted Tier 1 scores (`recall_at_k` 1.0, `mrr` 1.0),
  `k` 3, `tolerance` 0.05, plus human-readable notes.
- `README.md` — documents the dataset/baseline and "never lower the baseline to
  pass".

### Operator script

- `scripts/evaluate.py` — mirrors `scripts/ingest.py`. Default = Tier 1 score +
  regression gate (exit non-zero on regression). Flags: `--report-only`,
  `--update-baseline`, `--report PATH`, `--judge` (Tier 2, double opt-in).

### Tests

- `tests/unit/test_evaluation.py` — 38 hermetic tests (see §2).

### CI

- `.github/workflows/ci.yml` — new required job `eval` ("RAG eval regression gate
  (Tier 1)"): hermetic, key-free, runs `uv run python scripts/evaluate.py` under
  `ENVIRONMENT=test`.

### Docs / config

- `docs/adr/0017-rag-evaluation-and-regression-gate.md` (new) + index row in
  `docs/adr/README.md`.
- `docs/architecture.md` (eval section + updated non-goals/quality gate) and
  regenerated `architecture.html`.
- `docs/PROJECT_STATUS.md` (Stage 6 complete, progress 6/10, capabilities).
- `CLAUDE.md` (current state, layout, eval convention, deferred-table fix).
- `README.md` (brought current — see §12 deviations).
- `.gitignore` (un-ignore `data/eval/` — see §12).

---

## 2. Test count before/after

- **Command:** `uv run pytest`
- **Before:** `273 passed, 9 skipped`
- **After:** `311 passed, 9 skipped, 1 warning in 6.99s` (+38, all in
  `tests/unit/test_evaluation.py`)

The 9 skips are unchanged (opt-in live-datastore / live-Qdrant / live-provider
layers). No new network dependency; the full suite passes with zero real calls.

`tests/unit/test_evaluation.py` alone: `38 passed`.

---

## 3. ruff / format / mypy — exact commands and results

| Command | Result |
|---------|--------|
| `uv run ruff check .` | `All checks passed!` (exit 0) |
| `uv run ruff format --check .` | `73 files already formatted` (exit 0) |
| `uv run mypy` | `Success: no issues found in 72 source files` |

`scripts/` is inside mypy's `files`, so `scripts/evaluate.py` is type-checked strict too.

---

## 4. CLAUDE.md line count + deferred-table fix

- **Before:** 149 lines. **After:** 149 lines (`wc -l CLAUDE.md`). Under 150.
- **Deferred-table fix made:** yes. The Stage 6 row (which had listed "OTel
  metrics export") is gone; **OTel metrics export now sits in the Stage 9 row**
  ("Load/chaos testing, SLOs, **OTel metrics export** (traces-only shipped, ADR
  0016), …"). The same move is reflected in `docs/PROJECT_STATUS.md` and
  `docs/architecture.md`. Rationale: Stage 9 owns SLOs and actually needs metrics;
  `PROJECT_STATUS.md` (canonical) always defined Stage 6 as MLOps/eval only.

---

## 5. recall@k / MRR metric choice (→ ADR 0017)

- **recall@k** — of the documents that should have been retrieved, what fraction
  appear in the top `k`. Answers "did we find the right source at all".
- **MRR** — mean of 1/(rank of the first relevant document). Answers "did we rank
  it high", which recall alone is blind to.

Both are standard, cheap, threshold-free, and — crucially for a *deterministic*
gate — independent of any model's opinion. Computed at **document** granularity
(several chunks of one document collapse to its best rank). Full rationale and the
CI-vs-opt-in split are in **ADR 0017**.

---

## 6. Eval dataset — cases and how "expected relevant doc" was chosen

8 cases, 2 per corpus document. Each query is worded around vocabulary
**distinctive** to its target document so the lexical offline-hash embeddings can
rank it first; the `note` field records the disambiguation. Verified empirically —
all 8 retrieve their expected document at rank 1 (recall@3 = 1.0, MRR = 1.0).

| # | Query (abridged) | Expected doc | Why (from the `note`) |
|---|------------------|--------------|-----------------------|
| 1 | rollback window after a blue/green cutover | `deployments.md` | "rollback window"/"cutover" appear nowhere else. |
| 2 | redeploy the old build from source to roll back | `deployments.md` | "redeploy from source" is unique here (disambiguates from incident-response, which also mentions rolling back). |
| 3 | severity of a service returning wrong data | `incident-response.md` | Only doc defining severity levels; "Sev 1" unique. |
| 4 | job of the incident commander | `incident-response.md` | "incident commander" unique to this doc. |
| 5 | credentials/tokens in a log line | `observability.md` | The log-specific "never goes in a log" rule (credentials AND tokens AND log). |
| 6 | alert on CPU vs symptoms customers feel | `observability.md` | "alert"+"CPU"+"symptoms" co-occur only here. |
| 7 | how small should a pull-request diff be | `code-review.md` | "diff"/"reviewer"/small-change argument unique here. |
| 8 | what must block a code review | `code-review.md` | Explicit "What must block" section unique here. |

---

## 7. Regression baseline mechanism + location

- **File:** `data/eval/baseline.json` — `metrics` (`recall_at_k`, `mrr`), `k`,
  `tolerance`, and human notes.
- **Gate:** a run **fails** when any baseline metric drops below `baseline −
  tolerance` (floor 0.95 at tolerance 0.05). A missing metric counts as 0.0 (fail
  loud, not skip).
- **Human-owned:** a passing run never rewrites the file. Raising the bar is a
  deliberate `--update-baseline` action; verified idempotent (re-running produces
  byte-identical content bar platform line-endings).

`scripts/evaluate.py` (default) run this session: **exit 0**, both metrics `ok`
against the baseline.

---

## 8. The CI gate actually fails on a regressed input — mechanism, not claim

`tests/unit/test_evaluation.py::TestRegressionGate::test_a_regressed_run_fails_against_the_baseline`:
it runs the **real** `RetrievalEvaluator` over the **real** shipped dataset but
with a deliberately corrupted retriever (`_FixedRetriever([_doc("nonexistent.md")])`)
that answers every query with a document not in the corpus. Observed: `recall_at_k
== 0.0`, `mrr == 0.0`, and `check_regression(...).passed is False` against the
loaded baseline. Additional unit cases confirm a within-tolerance drop passes, a
below-tolerance drop fails, and a missing metric fails. All pass.

---

## 9. architecture.md updated + regenerated

- Edited `docs/architecture.md` (header → Stage 6, new "Evaluation and the
  regression gate" section, updated quality-gate test count and non-goals, ADR
  0017 in See-also).
- **Regenerated:** `uv run python scripts/build_architecture.py` → "Wrote
  architecture.html". No diagram source changed (no Node needed).
- **Drift check:** `uv run python scripts/build_architecture.py --check` →
  "architecture.html is up to date." (`tests/unit/test_architecture.py` also
  passes.)

---

## 10. Container boot — both profiles, and eval NOT in startup

Image built: `docker build -t production-llm-platform/api:stage06 .` (succeeded).
Datastores reached over the compose network `production-llm-platform_platform`
(postgres/redis/qdrant already up). My two containers were torn down afterwards;
the compose stack was left as found.

**prod profile** (`-e ENVIRONMENT=prod`, all three datastore URLs, dummy keys, host
port 8010):

```
/health  -> {"status":"ok","service":"api","version":"0.1.0","environment":"prod"}
/ready   -> {"status":"ready","checks":{"postgres":"ok","redis":"ok","qdrant":"ok"}}
/version -> {"service":"api","version":"0.1.0","environment":"prod"}
/metrics -> # HELP http_requests_total Total HTTP requests processed.
```

**test profile** (`-e ENVIRONMENT=test`, no datastores, no key, host port 8011):

```
/health -> {"status":"ok","service":"api","version":"0.1.0","environment":"test"}
chat    -> {"object":"chat.completion", ... "content":"You said: ping" ... "citations":[]}
SSE     -> data: {"object":"chat.completion.chunk", ... "content":"You"} ... data: [DONE]
```

**Eval NOT wired into startup — confirmed.** Both containers' boot logs were
grepped for `evaluat|recall_at_k|baseline|judge` → **NONE**. The prod boot log
shows only `service.startup`, tracing config, `llm.client_selected`,
`retrieval.tool_disabled`, `conversation.store_selected`, and three
`datastore.connected` events. The eval harness is a script only, exactly as
intended.

---

## 11. Tier 2 (LLM-as-judge) — was it run?

**No.** Tier 2 makes real, billable Anthropic calls and requires explicit human
confirmation (`RUN_LLM_JUDGE=1` + a key, non-`test` profile). It was **not run**
this session — no cost incurred. Its pure logic (`build_prompt`, `parse_verdict`)
and its `score`/`generate_grounded_answer` paths are covered hermetically via the
scripted double, so the code is exercised without a network call.

---

## 12. Deviations from scope

1. **`.gitignore` change (necessary, in-scope-adjacent).** `.gitignore` had
   `data/*` with negations only for `data/README.md` and `data/corpus/`, so
   `data/eval/` would have been ignored — the CI gate and the offline tests read
   those files from disk, so they must be tracked. Added `!data/eval/` with a
   comment. Without this the required CI job would have no dataset.
2. **README brought forward two stages.** The README banner/roadmap were still at
   **Stage 4** — Stage 5 (observability) never synced them (the exact "README
   drifts silently" failure `contributing.md` warns about). Since README is a
   required sync target, I updated it to Stage 6, folding in the Stage 5
   observability facts as well as Stage 6. These Stage 5 statements are descriptive
   of already-committed work, not re-verified this session.

No other deviations. The `security` stub folder was untouched.

---

## 13. Known limitations / deliberately deferred

- **Tier 1 measures lexical retrieval, not semantic.** The offline-hash embeddings
  score by lexical overlap, so the gate cannot catch a semantic-ranking regression
  that Voyage would; the dataset is built to be lexically answerable on purpose. A
  Voyage-backed tier is the natural future extension (ADR 0017).
- **Baseline sits at 1.0/1.0.** Legitimate for a deterministic pipeline over a
  well-separated corpus; the gate still bites because any single case regressing
  drops the mean below the 0.95 floor (proven by the regressed-input test).
- **Small dataset (8 cases).** Enough to gate against regression on this corpus,
  not a benchmark; it grows with the corpus.
- **Tier 2 is advisory and un-run here** — cost + non-determinism keep it opt-in
  and out of CI (ADR 0015/0017).
- **`--update-baseline` writes platform line-endings** (CRLF on this Windows box).
  Cosmetic; the checked-in file is LF and values are byte-identical on re-run.
