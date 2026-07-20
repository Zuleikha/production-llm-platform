"""The retrieval evaluator and the hermetic offline pipeline it grades.

Two pieces live here:

- :class:`RetrievalEvaluator` — the concrete :class:`~services.evaluation.base.Evaluator`.
  It grades a dataset against **any** ``Retriever`` (the real
  :class:`~services.retrieval.retriever.VectorRetriever`, or a stub in a test),
  because it depends only on the narrow ``Retriever`` protocol. That is what lets
  the same evaluator run the CI-blocking Tier 1 offline pipeline *and* be unit
  tested against a hand-built ranking.

- :class:`InMemoryCosineStore` + :func:`build_offline_retriever` — the **Tier 1**
  pipeline: the shipped corpus, chunked and embedded with the deterministic
  offline hashing embeddings the ``test`` profile already uses (ADR 0011), stored
  in a brute-force in-memory cosine index. No Qdrant, no Voyage, no network — the
  gate is hermetic by construction and runs in CI with no keys and no containers
  (ADR 0017).

The store is real code, not a test double: it computes genuine cosine similarity
over stored vectors and ranks by it, so a Tier 1 score reflects retrieval actually
working, not a fixture returning a canned order. It is in-memory because the eval
corpus is tiny and a brute-force scan is instant — standing up Qdrant would add a
dependency the gate exists to avoid, and prove nothing the ``TestAgainstRealQdrant``
integration layer does not already prove about Qdrant itself.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from shared.logging import get_logger
from shared.observability import traced

from services.evaluation import metrics
from services.evaluation.base import CaseResult, EvalCase, Evaluator, RetrievalReport
from services.evaluation.dataset import parse_case
from services.retrieval.base import RetrievedDocument, VectorStore
from services.retrieval.embeddings import HashingEmbeddingsClient
from services.retrieval.ingest import ingest_corpus, load_corpus
from services.retrieval.retriever import VectorRetriever

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from shared.config import Settings

    from services.retrieval.base import DocumentChunk, Retriever

_logger = get_logger("evaluation.retrieval")

# How many chunks to pull per query before reducing to ranked documents. Larger
# than the reported k so document-level recall@k is measured fairly even when one
# document contributes several nearby chunks. The eval corpus is small, so this is
# comfortably above the total chunk count.
_RETRIEVE_CHUNKS: Final[int] = 10


class InMemoryCosineStore(VectorStore):
    """A :class:`VectorStore` that ranks by real cosine similarity, in memory.

    Backs the Tier 1 evaluator. Unlike ``tests.fakes.InMemoryVectorStore`` (which
    returns documents in insertion order and computes nothing), this one does the
    arithmetic — so it can stand in for Qdrant's *ranking* in a hermetic gate
    without standing up Qdrant.
    """

    def __init__(self) -> None:
        self._chunks: list[tuple[tuple[float, ...], DocumentChunk]] = []

    async def ensure_collection(self) -> None:
        """No-op: an in-memory list has no collection to create."""

    async def upsert(self, chunks: Sequence[DocumentChunk]) -> None:
        """Store each chunk with its (unit-normalised) embedding for scoring."""
        for chunk in chunks:
            self._chunks.append((_unit(chunk.embedding), chunk))

    async def query(self, embedding: Sequence[float], *, top_k: int) -> Sequence[RetrievedDocument]:
        """Return the ``top_k`` chunks by cosine similarity to ``embedding``."""
        probe = _unit(embedding)
        scored = [(_dot(probe, vector), chunk) for vector, chunk in self._chunks]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            RetrievedDocument(
                id=chunk.id,
                text=chunk.text,
                score=score,
                document_id=chunk.document_id,
                source=chunk.source,
                position=chunk.position,
            )
            for score, chunk in scored[:top_k]
        ]


class RetrievalEvaluator(Evaluator):
    """Grades a dataset of queries against a ``Retriever``, document-level.

    For each case it retrieves, reduces the retrieved chunks to a ranked list of
    unique source documents, and computes recall@k and reciprocal rank against the
    case's expected documents. The aggregate report's ``metrics`` are the means —
    ``recall_at_k`` and ``mrr`` — and those are the numbers the regression gate
    compares (ADR 0017).
    """

    def __init__(self, retriever: Retriever, *, k: int = 3) -> None:
        if k <= 0:
            raise ValueError(f"k must be positive, got {k}")
        self._retriever = retriever
        self._k = k

    @property
    def k(self) -> int:
        return self._k

    async def evaluate(self, dataset: Sequence[Mapping[str, object]]) -> Mapping[str, float]:
        """Contract path: grade generic mappings, return the aggregate metrics.

        Parses each mapping through the same validation the dataset file uses,
        then delegates to :meth:`run`. Returns only the ``{metric: score}``
        mapping the :class:`Evaluator` seam promises; use :meth:`run` for the
        per-case detail.
        """
        cases = [parse_case(entry, index) for index, entry in enumerate(dataset)]
        report = await self.run(cases)
        return report.metrics

    @traced
    async def run(self, cases: Sequence[EvalCase]) -> RetrievalReport:
        """Grade ``cases`` and return the full per-case-plus-aggregate report."""
        results: list[CaseResult] = []
        for case in cases:
            documents = await self._retriever.retrieve(case.query, top_k=_RETRIEVE_CHUNKS)
            ranked = _ranked_document_ids(documents)
            results.append(
                CaseResult(
                    query=case.query,
                    relevant_document_ids=case.relevant_document_ids,
                    retrieved_document_ids=ranked,
                    recall_at_k=metrics.recall_at_k(ranked, case.relevant_document_ids, k=self._k),
                    reciprocal_rank=metrics.reciprocal_rank(ranked, case.relevant_document_ids),
                )
            )

        aggregate = {
            "recall_at_k": metrics.mean([r.recall_at_k for r in results]),
            "mrr": metrics.mean([r.reciprocal_rank for r in results]),
        }
        _logger.info(
            "evaluation.completed",
            extra={"cases": len(results), "k": self._k, **aggregate},
        )
        return RetrievalReport(metrics=aggregate, cases=tuple(results), k=self._k)


def _ranked_document_ids(documents: Sequence[RetrievedDocument]) -> tuple[str, ...]:
    """Ranked, de-duplicated source document ids, best first.

    Several chunks of one document collapse to that document's first (highest-
    ranked) appearance, because the metric grades whether the right *source* was
    found, not how many of its chunks were.
    """
    seen: dict[str, None] = {}
    for document in documents:
        seen.setdefault(document.document_id, None)
    return tuple(seen)


@traced
async def build_offline_retriever(settings: Settings, corpus: Path) -> VectorRetriever:
    """Assemble the hermetic Tier 1 retriever over ``corpus``.

    Deterministic offline hashing embeddings (ADR 0011) + an in-memory cosine
    store, so this makes no network call on any profile — the property the CI gate
    needs. ``min_score`` is 0 on purpose: the evaluator measures *ranking*, and the
    production score floor (which drops weak matches so "found nothing" is
    representable, ADR 0013) would confound a recall measurement by hiding a
    correct-but-low-scoring document.
    """
    embeddings = HashingEmbeddingsClient(dimensions=settings.voyage_embedding_dimensions)
    store = InMemoryCosineStore()
    documents = load_corpus(corpus)
    report = await ingest_corpus(
        documents,
        embeddings=embeddings,
        store=store,
        chunk_size=settings.chunk_size_tokens,
        chunk_overlap=settings.chunk_overlap_tokens,
    )
    _logger.info(
        "evaluation.corpus_indexed",
        extra={"documents": report.documents, "chunks": report.chunks},
    )
    return VectorRetriever(embeddings, store, top_k=_RETRIEVE_CHUNKS, min_score=0.0)


def _unit(vector: Sequence[float]) -> tuple[float, ...]:
    """L2-normalise so a dot product is a cosine. A zero vector is returned as-is."""
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return tuple(vector)
    return tuple(value / norm for value in vector)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))
