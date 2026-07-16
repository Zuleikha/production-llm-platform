"""The Qdrant vector store.

Qdrant has been connected and probed since Stage 2 but has held no data. This is
the module that puts data in it.

Two details here are the difference between working and a 400, and neither is
guessable from the client's type hints — see ADR 0012:

**Point ids must be unsigned integers or UUIDs.** ``PointStruct.id`` is typed
``int | str | UUID``, which reads like any string will do. It will not: the
*server* rejects an arbitrary string such as ``handbook.md:3``. Chunk ids are
therefore mapped onto a **UUIDv5** — deterministic, so re-ingesting the same
chunk updates its point in place rather than duplicating it, which is what makes
ingestion idempotent.

**The collection's vector size is fixed at creation.** It has to match the
embeddings model's output dimensionality, and nothing at runtime checks that for
you: point a 1024-dim collection at a 512-dim model and writes fail, while a
model swap that keeps the dimension silently destroys recall instead. Hence
:meth:`QdrantVectorStore.ensure_collection` takes the dimension from the
embeddings client itself rather than from a second setting that could drift.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final

from qdrant_client import AsyncQdrantClient, models
from shared.logging import get_logger
from shared.observability import traced

from services.retrieval.base import DocumentChunk, RetrievedDocument, VectorStore

if TYPE_CHECKING:
    from collections.abc import Sequence

_logger = get_logger("retrieval.store")

# Namespace for deriving point UUIDs from chunk ids. Any fixed UUID works; what
# matters is that it never changes, or every existing point is orphaned.
_POINT_NAMESPACE: Final[uuid.UUID] = uuid.UUID("6f9b1f4e-3d2a-5c7b-9e10-4a8d2c6b5f31")

# Qdrant accepts large batches, but a single oversized HTTP body is a poor
# failure mode; ingestion of a real corpus is chunked to this.
_UPSERT_BATCH: Final[int] = 256


def point_id_for(chunk_id: str) -> str:
    """Map a human-readable chunk id onto the UUID Qdrant will accept.

    Deterministic: the same chunk id always yields the same point, so a re-ingest
    is an update rather than a duplicate. See the module docstring.
    """
    return str(uuid.uuid5(_POINT_NAMESPACE, chunk_id))


class QdrantVectorStore(VectorStore):
    """A :class:`VectorStore` backed by a live Qdrant collection."""

    def __init__(self, client: AsyncQdrantClient, *, collection: str, dimensions: int) -> None:
        self._client = client
        self._collection = collection
        self._dimensions = dimensions

    @traced
    async def ensure_collection(self) -> None:
        """Create the collection with the right vector size, if it is missing.

        Idempotent, and deliberately does **not** recreate an existing
        collection: that would silently delete every vector in it. A collection
        that exists with the wrong dimension surfaces as a write failure, which
        is the louder and more recoverable outcome.
        """
        if await self._client.collection_exists(self._collection):
            return
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config=models.VectorParams(
                size=self._dimensions,
                # Cosine, because the embeddings are normalised and we care
                # about direction (topical similarity), not magnitude. See
                # ADR 0012.
                distance=models.Distance.COSINE,
            ),
        )
        _logger.info(
            "retrieval.collection_created",
            extra={"collection": self._collection, "dimensions": self._dimensions},
        )

    @traced
    async def upsert(self, chunks: Sequence[DocumentChunk]) -> None:
        """Write embedded chunks, in batches."""
        if not chunks:
            return
        for start in range(0, len(chunks), _UPSERT_BATCH):
            batch = chunks[start : start + _UPSERT_BATCH]
            await self._client.upsert(
                collection_name=self._collection,
                points=[
                    models.PointStruct(
                        id=point_id_for(chunk.id),
                        vector=list(chunk.embedding),
                        payload={
                            # `chunk_id` is the human-readable id; the point's own
                            # id is its UUID, which nothing outside this module
                            # should have to know about.
                            "chunk_id": chunk.id,
                            "text": chunk.text,
                            "document_id": chunk.document_id,
                            "source": chunk.source,
                            "position": chunk.position,
                        },
                    )
                    for chunk in batch
                ],
                wait=True,
            )
        _logger.info(
            "retrieval.upserted",
            extra={"collection": self._collection, "chunks": len(chunks)},
        )

    @traced
    async def query(self, embedding: Sequence[float], *, top_k: int) -> Sequence[RetrievedDocument]:
        """Return the ``top_k`` nearest chunks."""
        response = await self._client.query_points(
            collection_name=self._collection,
            query=list(embedding),
            limit=top_k,
            with_payload=True,
        )
        return [self._as_document(point) for point in response.points]

    @staticmethod
    def _as_document(point: models.ScoredPoint) -> RetrievedDocument:
        """Rebuild a :class:`RetrievedDocument` from a scored point.

        The payload is whatever was written — possibly by an older version of
        this code — so read it defensively rather than trusting its shape.
        """
        payload: dict[str, Any] = point.payload or {}
        return RetrievedDocument(
            id=str(payload.get("chunk_id", point.id)),
            text=str(payload.get("text", "")),
            score=float(point.score),
            document_id=str(payload.get("document_id", "")),
            source=str(payload.get("source", "")),
            position=int(payload.get("position", 0) or 0),
        )
