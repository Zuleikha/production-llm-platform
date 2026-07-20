"""The evaluation contract and its result types.

**Implemented in Stage 6 (MLOps).** Stage 1 shipped :class:`Evaluator` as a stub
whose ``evaluate`` raised ``NotImplementedError``; the concrete retrieval
evaluator now lives in :mod:`~services.evaluation.retrieval`, the deterministic
metrics in :mod:`~services.evaluation.metrics`, and the regression gate in
:mod:`~services.evaluation.baseline`.

Scope is deliberately narrow (ADR 0017): this evaluates the **RAG retrieval
pipeline** against a fixed, checked-in dataset of query → expected-relevant
document ids. It does not score agent tool-use or open-ended chat quality — those
have no fixture corpus to grade against and are out of scope this stage.

Two result shapes rather than one, because they answer different questions:

- :class:`CaseResult` is *per query*: what was retrieved, and the two metrics
  computed for it. It is what a human reads to see **why** a score moved.
- :class:`RetrievalReport` is the *aggregate*: the mean metrics across every
  case, plus the per-case detail. Its ``metrics`` mapping is exactly what the
  :class:`Evaluator` contract returns and what the regression gate compares.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One graded query: the question, and which documents should answer it.

    ``relevant_document_ids`` holds ``document_id`` values (e.g.
    ``"deployments.md"``), never chunk ids — the metric is "did retrieval surface
    the right *source*", and a document may be chunked into several pieces. A case
    with an empty relevant set is meaningless to grade and is rejected at load
    time (see :mod:`~services.evaluation.dataset`).
    """

    query: str
    relevant_document_ids: frozenset[str]
    # Why this document is the expected answer. Kept so the dataset is auditable
    # rather than arbitrary (a stage-6 requirement), never used in scoring.
    note: str = ""


@dataclass(frozen=True, slots=True)
class CaseResult:
    """The outcome of grading one :class:`EvalCase`.

    ``retrieved_document_ids`` is the ranked, **de-duplicated** list of source
    documents retrieval returned, best first — several chunks of one document
    collapse to that document's first appearance, because the metric is
    document-level.
    """

    query: str
    relevant_document_ids: frozenset[str]
    retrieved_document_ids: tuple[str, ...]
    recall_at_k: float
    reciprocal_rank: float


@dataclass(frozen=True, slots=True)
class RetrievalReport:
    """The aggregate result of one evaluation run.

    ``metrics`` is the mean of each per-case metric across ``cases`` — the numbers
    the regression baseline is compared against. ``k`` is recorded because
    ``recall_at_k`` is meaningless without the ``k`` it was measured at.
    """

    metrics: Mapping[str, float]
    cases: tuple[CaseResult, ...]
    k: int


class Evaluator(ABC):
    """Scores system outputs against a dataset of cases."""

    @abstractmethod
    async def evaluate(self, dataset: Sequence[Mapping[str, object]]) -> Mapping[str, float]:
        """Run evaluation over ``dataset`` and return named metric scores.

        The generic ``Mapping`` signature is the Stage 1 contract, kept so the
        seam is stable. Concrete evaluators accept their own richer, typed case
        type as well — see :meth:`RetrievalEvaluator.run` — and return the same
        aggregate ``{metric_name: score}`` mapping from both paths.
        """
        raise NotImplementedError
