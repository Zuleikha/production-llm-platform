# ADR 0017 — RAG evaluation: recall@k/MRR, a two-tier CI-vs-opt-in split, and a checked-in regression baseline

- **Status:** Accepted
- **Date:** 2026-07-20
- **Stage:** 6 (MLOps)
- **Builds on:** ADR 0009/0011 (hermetic-by-construction provider seams), ADR
  0013 (retrieval score floor and citations), ADR 0015 (opt-in live provider
  test).

## Context

Stage 4 shipped a RAG pipeline — chunk, embed, store, retrieve, cite — but nothing
measures whether retrieval is any *good*, or catches the day a refactor quietly
makes it worse. Stage 6's job is to give that pipeline a real, repeatable
evaluation harness and turn one tier of it into a regression gate CI can block on.

Three things had to be decided together:

1. **What to measure, and how.**
2. **What is safe to run in CI on every push, and what is not.**
3. **How a "regression" is defined so the gate is neither flaky nor toothless.**

Two constraints from earlier ADRs shape all three. CI is hermetic, free and
key-free (ADR 0009/0011): whatever blocks a build cannot call a paid API or need a
secret. And the `test` profile already ships a deterministic offline embeddings
double (`HashingEmbeddingsClient`, ADR 0011) whose vectors have genuine cosine
similarity driven by lexical overlap — a ready-made way to score retrieval with
zero network.

## Decision

### 1. Metrics: recall@k and MRR, at document granularity

The evaluator grades a dataset of `query → expected-relevant-document-id` cases and
reports two metrics:

- **recall@k** — of the documents that should have been retrieved, what fraction
  appear in the top `k`. Answers "did we find the right source at all".
- **MRR** (mean reciprocal rank) — 1/(rank of the first relevant document),
  averaged. Answers "did we rank it *high*", which recall alone is blind to.

They are computed at **document** granularity, not chunk: a document split into
several chunks must not count several times, and the question a citation answers is
"which source", so several retrieved chunks of one document collapse to that
document's best rank. Both are standard, cheap, threshold-free retrieval metrics
with no dependency on a model's opinion — exactly what a deterministic gate wants.
They live as pure functions in `services/evaluation/metrics.py` so they are
hand-verifiable in a test (given a ranking and a relevant set, the float is
checkable on paper), which a metric buried inside an I/O method would not be.

### 2. Two tiers, split by what is CI-safe

- **Tier 1 — deterministic retrieval metrics.** recall@k and MRR computed over the
  shipped corpus using the offline hashing embeddings and an in-memory brute-force
  cosine store (`InMemoryCosineStore`). Free, hermetic, zero network — no Qdrant,
  no Voyage, no key. **This is the CI-blocking regression gate.** The store is real
  code that computes genuine cosine similarity and ranks by it (not a fixture
  returning a canned order), so a Tier 1 score reflects retrieval actually working;
  it is in-memory only because the eval corpus is tiny and standing up Qdrant would
  add the very dependency the gate exists to avoid — and `TestAgainstRealQdrant`
  (ADR 0012) already proves Qdrant ranks.

- **Tier 2 — LLM-as-judge.** Answer faithfulness and citation accuracy, scored by a
  real Anthropic call. **Opt-in and manually triggered only**, the exact treatment
  of the live contract test (ADR 0015): double opt-in (`RUN_LLM_JUDGE=1` *and* a
  key), built from a non-`test` profile because the `test` profile cannot construct
  a real client at all (ADR 0009), never in CI, never without human confirmation.
  It is advisory — it does not gate.

The split falls exactly on the ADR 0009/0011 line: what cannot spend money blocks
the build; what can, never does.

### 3. Regression baseline: checked-in, human-updated, tolerance-gated

The last-accepted Tier 1 scores live in `data/eval/baseline.json` (scores, the `k`
they were measured at, and a `tolerance`). A run **fails the gate** when any
baseline metric drops below `baseline − tolerance`. Two deliberate asymmetries:

- **Only downward moves fail**, and a passing run **never rewrites the file**.
  Raising the bar is a reviewed human decision (`--update-baseline`, someone
  confirms the gain is real) — otherwise the gate would ratchet itself up on noise
  and stop catching anything, or ratchet down and pass everything.
- **A missing metric fails loud**, not silently: a baseline naming `mrr` against a
  run that reports none is a broken evaluator, so it fails rather than skips
  (CLAUDE.md).

The Tier 1 pipeline is fully deterministic, so today `tolerance` absorbs only float
noise. It is honoured anyway so the mechanism is already correct the day a
non-deterministic tier (a real Voyage-backed eval, or Tier 2 promoted to gating)
introduces genuine variance.

### The harness is a script, not a route

`scripts/evaluate.py`, the same shape as `scripts/ingest.py`: an operator/CI-time
tool, never a boot hook, never wired into the running service. The container-boot
check for this stage confirms exactly that — the eval harness must **not** appear
in app startup.

### The dataset

Eight cases, two per corpus document, checked in at `data/eval/dataset.json`. Each
query is worded around vocabulary distinctive to its target document, and each
carries a `note` recording *why* that document is the expected answer — the dataset
is auditable, not arbitrary. Because Tier 1's embeddings are lexical, the dataset is
answerable by lexical retrieval by construction; a semantic dataset that only
Voyage could satisfy is the natural extension when a Voyage-backed tier is added.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **LLM-as-judge as the CI gate** | Costs money per push, needs a key in CI, and is non-deterministic — everything ADR 0009 rejects for a blocking check. Kept as opt-in Tier 2. |
| **Evaluate against a live Qdrant in CI** | Adds a service container the hermetic gate exists to avoid, and proves nothing about *ranking* that `InMemoryCosineStore` (same cosine, same vectors) doesn't. Qdrant's own acceptance is already covered by `TestAgainstRealQdrant`. |
| **Auto-update the baseline on a green run** | The gate would ratchet itself and stop catching regressions. Updating the bar is a deliberate, reviewed human action. |
| **A pass/fail threshold instead of a baseline** | A fixed threshold is either so loose it never fires or so tight it blocks legitimate corpus changes. A baseline-plus-tolerance tracks the current accepted state and flags *movement*. |
| **`nDCG` / precision@k as well** | More metrics, no new signal at this corpus size (one relevant document per case). recall@k answers "found it", MRR answers "ranked it high"; that is enough to catch a regression. Revisit with graded relevance. |
| **A new API route for evaluation** | Evaluation is a dev/CI-time concern, not a product surface. A script keeps it off the running service entirely (mirrors `scripts/ingest.py`). |

## Consequences

**Positive**

- A retrieval regression now fails CI on a hermetic, free, key-free job held to the
  same standard as the rest of the suite.
- The metrics are deterministic and hand-verifiable, so a red gate is a real signal,
  not flake.
- Tier 2 gives a paid, higher-fidelity answer-quality read when a human wants it,
  without ever putting cost or a key on the critical path.

**Negative / accepted trade-offs**

- **Tier 1 measures lexical retrieval, not semantic.** The offline embeddings score
  by lexical overlap, so the gate cannot catch a semantic-ranking regression that
  Voyage would — the dataset is built to be lexically answerable on purpose. A
  Voyage-backed tier is the future extension; deferred, not pretended.
- **The baseline currently sits at 1.0/1.0.** A deterministic pipeline over a
  well-separated corpus legitimately scores perfectly; the gate still bites, because
  any single case regressing drops the mean below the 0.95 floor. Verified by a test
  that fails the gate on a deliberately corrupted retrieval.
- **Tier 2's cost and non-determinism** are real; both are contained by the double
  opt-in and its exclusion from CI (ADR 0015).
- **Small dataset (8 cases).** Enough to gate against regression on this corpus, not
  a benchmark. It grows with the corpus.
