"""The retriever: a question in, ranked source chunks out.

Thin on purpose. Embedding lives behind :class:`EmbeddingsClient`, search behind
:class:`VectorStore`, and this is the two-line composition of them — which is
what lets the agent's retrieval tool depend on the narrow ``Retriever`` protocol
and know about neither Voyage nor Qdrant.

The one judgement it makes is the **score floor**. A vector search always returns
its ``top_k`` nearest neighbours, however far away they are: ask an unrelated
question and you still get the ``k`` least-unrelated chunks back, each with a low
score. Handing those to the model as grounding is how a RAG system cites sources
that do not support its answer. Below ``min_score`` a chunk is dropped, so "I
found nothing relevant" is representable — see ADR 0013.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.logging import get_logger
from shared.observability import traced

from services.retrieval.embeddings import build_embeddings_client
from services.retrieval.store import QdrantVectorStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from qdrant_client import AsyncQdrantClient
    from shared.config import Settings

    from services.retrieval.base import RetrievedDocument, VectorStore
    from services.retrieval.embeddings import EmbeddingsClient

_logger = get_logger("retrieval.retriever")


class VectorRetriever:
    """A :class:`~services.retrieval.base.Retriever` over an embeddings client and a store."""

    def __init__(
        self,
        embeddings: EmbeddingsClient,
        store: VectorStore,
        *,
        top_k: int = 4,
        min_score: float = 0.15,
    ) -> None:
        self._embeddings = embeddings
        self._store = store
        self._top_k = top_k
        self._min_score = min_score

    @traced
    async def retrieve(
        self, query: str, *, top_k: int | None = None
    ) -> Sequence[RetrievedDocument]:
        """Return chunks relevant to ``query``, best first, weak matches dropped.

        ``top_k=None`` means the value this retriever was configured with.
        """
        if not query.strip():
            return []
        embedding = await self._embeddings.embed_query(query)
        found = await self._store.query(
            embedding, top_k=top_k if top_k is not None else self._top_k
        )
        kept = [document for document in found if document.score >= self._min_score]
        _logger.info(
            "retrieval.retrieved",
            extra={"found": len(found), "kept": len(kept), "min_score": self._min_score},
        )
        return kept


@traced
def build_retriever(settings: Settings, client: AsyncQdrantClient | None) -> VectorRetriever | None:
    """Assemble a retriever, or None when there is no Qdrant to retrieve from.

    Returning None rather than raising mirrors how the datastores report an
    unconfigured store (ADR 0005): no Qdrant means the agent runs without the
    retrieval tool and answers ungrounded, exactly as it did in Stage 3, instead
    of the whole service failing to build an engine. Under `prod` this cannot
    happen — ``QDRANT_URL`` is mandatory at boot — so the degraded path is a dev
    and test convenience, not a production behaviour.

    The embeddings client is chosen by ``build_embeddings_client``, so the
    ``test`` profile gets the offline double here too (ADR 0011).
    """
    if client is None:
        return None
    embeddings = build_embeddings_client(settings)
    store = QdrantVectorStore(
        client,
        collection=settings.qdrant_collection,
        # From the client, not a second setting: the collection's vector size
        # must equal the model's output dimensionality, and two settings that
        # can disagree eventually will (ADR 0012).
        dimensions=embeddings.dimensions,
    )
    return VectorRetriever(embeddings, store, top_k=settings.retrieval_top_k)
