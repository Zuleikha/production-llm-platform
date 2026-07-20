# data/eval — the RAG evaluation dataset and regression baseline

> **Not application data, and — unlike `data/corpus/` — not ingested.** These
> files are read by `scripts/evaluate.py` and the evaluation service
> (`services/evaluation/`), never by `scripts/ingest.py`. They live outside
> `data/corpus/` on purpose, so `load_corpus` never sweeps them into Qdrant.

## Files

| File | What it is |
|------|-----------|
| `dataset.json` | The graded queries: each a natural-language question, the `document_id`(s) that should answer it, and a `note` recording **why** those are the expected answer. |
| `baseline.json` | The last-accepted Tier 1 scores (`recall_at_k`, `mrr`) plus the `k` and `tolerance` the CI regression gate uses. |

## How the dataset was chosen

Eight cases, two per corpus document. Each query is worded around vocabulary that
is **distinctive** to its target document (e.g. "rollback window" and "blue/green
cutover" for `deployments.md`), so the deterministic offline-hash retrieval the
Tier 1 gate uses (ADR 0011) can rank the right source first. The `note` on each
case states the disambiguating reasoning — the dataset is auditable, not
arbitrary (ADR 0017).

## Running

```bash
# Tier 1 — hermetic, deterministic, no keys. This is the CI gate.
uv run python scripts/evaluate.py

# Deliberately accept new scores as the baseline (a reviewed human action).
uv run python scripts/evaluate.py --update-baseline

# Tier 2 — LLM-as-judge. COSTS MONEY. Double opt-in, never in CI (ADR 0015/0017).
RUN_LLM_JUDGE=1 uv run python scripts/evaluate.py --judge
```

## Read this before editing

- **Do not lower `baseline.json` to make a red run pass.** A drop is the gate
  working. Fix retrieval, or — if the change is a genuine, intended improvement —
  raise the baseline deliberately with `--update-baseline` and review the diff.
- Keep `relevant_document_ids` as `document_id` values (e.g. `deployments.md`),
  never chunk ids: the metric grades whether the right *source* was retrieved.
