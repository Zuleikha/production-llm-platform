"""Ingestion: source documents in, embedded chunks in Qdrant out.

The pipeline is four steps, and each one is a seam something else already owns:

    read -> chunk (LlamaIndex) -> embed (EmbeddingsClient) -> store (VectorStore)

**Only LlamaIndex's ingestion primitives are used** — ``Document`` and
``SentenceSplitter`` — not its indices, retrievers, query engines or storage
contexts. That is deliberate. LlamaIndex's higher layers want to own the vector
store, the embeddings client and the query path, all three of which this platform
already owns behind its own seams (ADR 0005, 0011, 0012). Adopting them would
mean two competing abstractions over the same Qdrant collection. Chunking is the
part worth borrowing, because sentence-aware splitting with token-accurate
overlap is fiddly and LlamaIndex does it well. See ADR 0011.

**Chunk ids are deterministic** — ``{document_id}:{position}``. Re-ingesting an
unchanged corpus therefore updates each point in place instead of duplicating it
(see :func:`~services.retrieval.store.point_id_for`), which is what makes running
ingestion twice safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from shared.logging import get_logger
from shared.observability import traced

from services.retrieval.base import DocumentChunk

if TYPE_CHECKING:
    from collections.abc import Sequence

    from services.retrieval.base import VectorStore
    from services.retrieval.embeddings import EmbeddingsClient

_logger = get_logger("retrieval.ingest")

# What `load_corpus` will read. Everything else in the directory is ignored
# rather than guessed at — a .png fed to a text splitter is a silent mess.
_TEXT_SUFFIXES: Final[frozenset[str]] = frozenset({".md", ".txt"})


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """One document before chunking: its text and where it came from."""

    document_id: str
    source: str
    text: str


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """What one ingestion run did. Returned so a caller can assert on it."""

    documents: int
    chunks: int


@traced
def load_corpus(directory: Path) -> list[SourceDocument]:
    """Read every text document under ``directory``, recursively.

    Sorted, so ingestion order — and therefore chunk ids — are stable across
    machines and filesystems.

    Raises:
        FileNotFoundError: if ``directory`` does not exist. A corpus path that
            is wrong is a configuration error worth failing on, not an empty
            index to discover in production.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"corpus directory does not exist: {directory}")

    documents: list[SourceDocument] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        relative = path.relative_to(directory).as_posix()
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        documents.append(SourceDocument(document_id=relative, source=relative, text=text))
    return documents


@traced
def chunk_documents(
    documents: Sequence[SourceDocument], *, chunk_size: int, chunk_overlap: int
) -> list[tuple[SourceDocument, int, str]]:
    """Split documents into ``(document, position, text)`` triples.

    Returns triples rather than :class:`DocumentChunk` because a chunk is not
    complete until it has an embedding, and a type with a placeholder vector in
    it would be a lie the type checker could not catch.
    """
    splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks: list[tuple[SourceDocument, int, str]] = []
    for document in documents:
        nodes = splitter.get_nodes_from_documents([Document(text=document.text)])
        for position, node in enumerate(nodes):
            text = node.get_content().strip()
            if text:
                chunks.append((document, position, text))
    return chunks


@traced
async def ingest_corpus(
    documents: Sequence[SourceDocument],
    *,
    embeddings: EmbeddingsClient,
    store: VectorStore,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> IngestionReport:
    """Chunk, embed and store ``documents``. Idempotent for unchanged input.

    Embeds every chunk in one call rather than per document: the embeddings
    client batches internally, and a request per document would be both slower
    and more expensive for no benefit.
    """
    triples = chunk_documents(documents, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not triples:
        _logger.info("retrieval.ingest_empty", extra={"documents": len(documents)})
        return IngestionReport(documents=len(documents), chunks=0)

    vectors = await embeddings.embed_documents([text for _, _, text in triples])
    if len(vectors) != len(triples):  # pragma: no cover - a client that drops rows is broken
        raise RuntimeError(
            f"embeddings client returned {len(vectors)} vectors for {len(triples)} chunks"
        )

    await store.ensure_collection()
    await store.upsert(
        [
            DocumentChunk(
                id=f"{document.document_id}:{position}",
                text=text,
                embedding=tuple(vector),
                document_id=document.document_id,
                source=document.source,
                position=position,
            )
            for (document, position, text), vector in zip(triples, vectors, strict=True)
        ]
    )

    _logger.info(
        "retrieval.ingested",
        extra={"documents": len(documents), "chunks": len(triples)},
    )
    return IngestionReport(documents=len(documents), chunks=len(triples))
