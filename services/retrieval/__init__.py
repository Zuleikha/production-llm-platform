"""Retrieval service: ingestion, embeddings, vector search and the agent's tool."""

from __future__ import annotations

from services.retrieval.base import (
    DocumentChunk,
    RetrievedDocument,
    Retriever,
    VectorStore,
)
from services.retrieval.embeddings import EmbeddingsClient, build_embeddings_client
from services.retrieval.retriever import VectorRetriever, build_retriever
from services.retrieval.store import QdrantVectorStore
from services.retrieval.tool import DocumentSearch

__all__ = [
    "DocumentChunk",
    "DocumentSearch",
    "EmbeddingsClient",
    "QdrantVectorStore",
    "RetrievedDocument",
    "Retriever",
    "VectorRetriever",
    "VectorStore",
    "build_embeddings_client",
    "build_retriever",
]
