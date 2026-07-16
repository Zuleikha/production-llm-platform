"""The retrieval contracts: what gets stored, what comes back, and the seams.

**Implemented in Stage 4 (RAG).** Stage 1 shipped these as stubs raising
``NotImplementedError``; the concrete implementations now live in
:mod:`~services.retrieval.store` (Qdrant), :mod:`~services.retrieval.retriever`
and :mod:`~services.retrieval.ingest`.

Two types rather than one, which is a correction to the Stage 1 sketch.  That
sketch had ``VectorStore.upsert`` take a ``Sequence[RetrievedDocument]``
"(with embeddings)" — but ``RetrievedDocument`` carries a *relevance score* and
no vector, and a score is something a query produces, not something you can
write. Storing and retrieving are genuinely different shapes:

- :class:`DocumentChunk` is what you *put in*: text plus its embedding plus the
  provenance a citation is later built from.
- :class:`RetrievedDocument` is what you *get out*: a chunk, plus how well it
  matched.

``RetrievedDocument``'s original three fields (``id``, ``text``, ``score``) keep
their names and order — the Stage 1 contract is widened here, not broken.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """One embedded slice of a source document, ready to store.

    ``document_id``/``source``/``position`` are provenance, not decoration: they
    are what makes a citation traceable back to a place a human can open. A
    chunk that cannot say where it came from is not citable, so they are
    required rather than optional (ADR 0013).
    """

    id: str
    text: str
    embedding: tuple[float, ...]
    document_id: str
    source: str
    position: int


@dataclass(frozen=True, slots=True)
class RetrievedDocument:
    """A single retrieval result: document text plus a relevance score."""

    id: str
    text: str
    score: float
    document_id: str = ""
    source: str = ""
    position: int = 0


@runtime_checkable
class Retriever(Protocol):
    """Returns documents relevant to a query. Structural (duck-typed) contract."""

    async def retrieve(
        self, query: str, *, top_k: int | None = None
    ) -> Sequence[RetrievedDocument]:
        """Return up to ``top_k`` documents relevant to ``query``.

        ``top_k`` widens the Stage 1 signature's ``int = 5`` to ``int | None =
        None``, meaning "use the retriever's own configured default". The literal
        5 could not survive contact with an implementation that is *configured*
        with a top_k: a caller who omitted the argument would silently get 5
        rather than the configured value, and the configuration would be dead.
        Every existing caller passing an ``int`` still type-checks.
        """
        ...


class VectorStore(ABC):
    """Abstract vector store backing a :class:`Retriever`."""

    @abstractmethod
    async def ensure_collection(self) -> None:
        """Create the collection if it does not exist. Must be idempotent.

        Separate from ``upsert`` because the collection's vector size and
        distance metric are fixed at creation and shared by every writer — see
        ADR 0012.
        """

    @abstractmethod
    async def upsert(self, chunks: Sequence[DocumentChunk]) -> None:
        """Insert or update embedded chunks in the store."""

    @abstractmethod
    async def query(self, embedding: Sequence[float], *, top_k: int) -> Sequence[RetrievedDocument]:
        """Return the ``top_k`` nearest documents to ``embedding``."""
