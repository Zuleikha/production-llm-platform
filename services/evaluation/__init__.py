"""Evaluation service (Stage 6, MLOps).

Scores the RAG retrieval pipeline against a fixed, checked-in dataset. Tier 1
(deterministic recall@k / MRR, hermetic) is the CI-blocking regression gate; Tier
2 (LLM-as-judge) is opt-in and billable. See ADR 0017 and ``README.md``.
"""

from __future__ import annotations

from services.evaluation.base import (
    CaseResult,
    EvalCase,
    Evaluator,
    RetrievalReport,
)
from services.evaluation.baseline import (
    Baseline,
    RegressionResult,
    check_regression,
    load_baseline,
)
from services.evaluation.dataset import load_eval_cases
from services.evaluation.retrieval import (
    InMemoryCosineStore,
    RetrievalEvaluator,
    build_offline_retriever,
)

__all__ = [
    "Baseline",
    "CaseResult",
    "EvalCase",
    "Evaluator",
    "InMemoryCosineStore",
    "RegressionResult",
    "RetrievalEvaluator",
    "RetrievalReport",
    "build_offline_retriever",
    "check_regression",
    "load_baseline",
    "load_eval_cases",
]
