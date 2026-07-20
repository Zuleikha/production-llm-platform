"""The retrieval metrics: recall@k and reciprocal rank, as pure functions.

Kept apart from the evaluator that calls them, and kept free of any I/O, because
these are the two lines of arithmetic the whole regression gate rests on and they
must be *hand-verifiable in a test* (a Stage 6 requirement, ADR 0017). A function
that takes a ranked list and a relevant set and returns a float can be checked on
paper; the same logic buried inside a method that also embeds queries and talks to
a store cannot.

Both operate on a **ranked, de-duplicated list of document ids** (best first) and
a set of relevant document ids. Document-level, not chunk-level: the question the
metric answers is "did retrieval surface the right *source*", and a document split
into several chunks must not count several times (ADR 0017).

- **recall@k** — of the documents that *should* have been found, what fraction
  appear in the top ``k``. 1.0 means every relevant document was retrieved.
- **reciprocal rank** — 1 / (rank of the first relevant document), or 0 if none
  was retrieved at all. Rewards putting a right answer *high*, which recall alone
  is blind to. Averaged over a dataset this is MRR.
"""

from __future__ import annotations

from collections.abc import Sequence


def recall_at_k(ranked_document_ids: Sequence[str], relevant: frozenset[str], *, k: int) -> float:
    """Fraction of ``relevant`` documents appearing in the top ``k`` retrieved.

    Args:
        ranked_document_ids: retrieved document ids, best first, de-duplicated.
        relevant: the document ids that should have been retrieved. Never empty
            in a real case — a case with nothing relevant cannot be graded and is
            rejected at load time.
        k: how far down the ranking to look.

    Raises:
        ValueError: if ``k`` is not positive, or ``relevant`` is empty (dividing
            by zero relevant documents is a silent 0/0, not a real score).
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if not relevant:
        raise ValueError("recall_at_k is undefined for a case with no relevant documents")
    top_k = set(ranked_document_ids[:k])
    found = len(top_k & relevant)
    return found / len(relevant)


def reciprocal_rank(ranked_document_ids: Sequence[str], relevant: frozenset[str]) -> float:
    """1 / (1-based rank of the first relevant document), or 0.0 if none is found.

    Scans the whole ranking, not just a prefix: the rank of the first hit is the
    quantity, and truncating would turn "found it at position 5" into a false 0.
    """
    for index, document_id in enumerate(ranked_document_ids, start=1):
        if document_id in relevant:
            return 1.0 / index
    return 0.0


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean, or 0.0 for an empty sequence.

    An empty dataset scoring 0.0 rather than raising is deliberate: the regression
    gate reads these numbers, and 0.0 fails loudly against any real baseline,
    which is the right outcome for "the dataset vanished".
    """
    return sum(values) / len(values) if values else 0.0
