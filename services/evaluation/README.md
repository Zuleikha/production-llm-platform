# evaluation — RAG evaluation harness and regression gate

> **Status: implemented in Stage 6 (MLOps).** Stage 1 shipped only the
> `Evaluator` contract; the concrete retrieval evaluator, deterministic metrics,
> checked-in dataset, regression baseline and LLM-as-judge tier now live here.
> See **ADR 0017**.

Scope is deliberately narrow (ADR 0017): this evaluates the **RAG retrieval
pipeline** (Stage 4) against a fixed, checked-in dataset. It does **not** score
agent tool-use or open-ended chat quality — those have no fixture corpus to grade
against and are out of scope this stage.

## Two tiers, split by what CI can run

| Tier | What | Cost | In CI? |
|------|------|------|--------|
| **Tier 1** | Deterministic retrieval metrics — `recall@k`, `mrr` — over the shipped corpus using the offline-hash embeddings (ADR 0011) and an in-memory cosine store. | Free, hermetic, zero network. | **Yes — the CI-blocking regression gate.** |
| **Tier 2** | LLM-as-judge: answer faithfulness + citation accuracy, via real Anthropic calls. | **Costs money.** | **Never.** Opt-in, double-gated (ADR 0015). |

## Layout

| File | Purpose |
|------|---------|
| `base.py` | The `Evaluator` ABC + result types (`EvalCase`, `CaseResult`, `RetrievalReport`). |
| `metrics.py` | Pure, hand-verifiable `recall_at_k` / `reciprocal_rank` / `mean`. |
| `dataset.py` | Loads and strictly validates `data/eval/dataset.json`. |
| `retrieval.py` | `RetrievalEvaluator` (grades any `Retriever`), `InMemoryCosineStore`, and `build_offline_retriever` (the hermetic Tier 1 pipeline). |
| `baseline.py` | Loads `data/eval/baseline.json` and runs the regression gate (`check_regression`). |
| `judge.py` | Tier 2 `LLMJudge` + grounded-answer generation. Real calls outside `test`. |

## Contract

`Evaluator` (see `base.py`) scores system outputs against a dataset.

| Method | Signature | Meaning |
|--------|-----------|---------|
| `evaluate` | `async evaluate(dataset: Sequence[Mapping[str, object]]) -> Mapping[str, float]` | Return named metric scores. `RetrievalEvaluator.run` is the richer, typed path returning per-case detail. |

## Running

The harness is an operator/CI script (`scripts/evaluate.py`), never a service boot
hook — the same pattern as `scripts/ingest.py`.

```bash
# Tier 1 — the CI gate. Exits non-zero if scores drop below baseline − tolerance.
uv run python scripts/evaluate.py

# Accept new scores as the baseline (a deliberate, reviewed human action).
uv run python scripts/evaluate.py --update-baseline

# Tier 2 — LLM-as-judge. COSTS MONEY. Double opt-in, never in CI.
RUN_LLM_JUDGE=1 uv run python scripts/evaluate.py --judge
```

The dataset and baseline, and how each case's expected document was chosen, are
documented in `data/eval/README.md`.
