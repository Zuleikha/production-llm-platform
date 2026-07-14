"""Interface stub: retrieval / RAG contract.

**Planned, not yet implemented — Stage 4 (RAG).**

Defines the retrieval seam: a ``Retriever`` returns relevant documents for a
query, backed by a ``VectorStore`` (Qdrant in this platform). Concrete
implementations (LlamaIndex ingestion, embedding, Qdrant queries) arrive in
Stage 4. See ``README.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RetrievedDocument:
    """A single retrieval result: document text plus a relevance score."""

    id: str
    text: str
    score: float


@runtime_checkable
class Retriever(Protocol):
    """Returns documents relevant to a query. Structural (duck-typed) contract."""

    async def retrieve(self, query: str, *, top_k: int = 5) -> Sequence[RetrievedDocument]:
        """Return up to ``top_k`` documents relevant to ``query``."""
        ...


class VectorStore(ABC):
    """Abstract vector store backing a :class:`Retriever`."""

    @abstractmethod
    async def upsert(self, documents: Sequence[RetrievedDocument]) -> None:
        """Insert or update documents (with embeddings) in the store.

        Raises:
            NotImplementedError: Always, until implemented in Stage 4.
        """
        raise NotImplementedError

    @abstractmethod
    async def query(self, embedding: Sequence[float], *, top_k: int) -> Sequence[RetrievedDocument]:
        """Return the ``top_k`` nearest documents to ``embedding``.

        Raises:
            NotImplementedError: Always, until implemented in Stage 4.
        """
        raise NotImplementedError
