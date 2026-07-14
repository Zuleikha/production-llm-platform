# evaluation — interface stub

> **Status: planned, not yet implemented.** Delivered in **Stage 6 (MLOps /
> evaluation)**. Stage 1 ships only the contract below.

## Intended contract

`Evaluator` (see `base.py`) scores system outputs against a dataset.

| Method | Signature | Meaning |
|--------|-----------|---------|
| `evaluate` | `async evaluate(dataset: Sequence[Mapping[str, object]]) -> Mapping[str, float]` | Return named metric scores. |

## Planned scope (Stage 6)

- LLM-as-judge scoring, retrieval metrics (recall@k, MRR), regression gates.
- Offline eval datasets and CI quality thresholds.

The `evaluate` method currently raises `NotImplementedError`.
