"""Tests for the ingestion pipeline: read -> chunk -> embed -> store."""

from __future__ import annotations

from pathlib import Path

import pytest
from services.retrieval.embeddings import HashingEmbeddingsClient
from services.retrieval.ingest import (
    SourceDocument,
    chunk_documents,
    ingest_corpus,
    load_corpus,
)

from tests.fakes import InMemoryVectorStore

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = _REPO_ROOT / "data" / "corpus"


class TestLoadCorpus:
    def test_it_reads_the_shipped_corpus(self) -> None:
        documents = load_corpus(_CORPUS)
        assert {d.document_id for d in documents} == {
            "code-review.md",
            "deployments.md",
            "incident-response.md",
            "observability.md",
        }

    def test_the_corpus_readme_is_not_part_of_the_corpus(self) -> None:
        """It lives in data/, not data/corpus/, precisely so it is not ingested."""
        assert (_REPO_ROOT / "data" / "README.md").is_file()
        assert not (_CORPUS / "README.md").exists()

    def test_documents_are_ordered_stably(self) -> None:
        """Chunk ids derive from ingestion order; unstable order would churn them."""
        ids = [d.document_id for d in load_corpus(_CORPUS)]
        assert ids == sorted(ids)

    def test_it_reads_nested_directories(self, tmp_path: Path) -> None:
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "deep.md").write_text("content here", encoding="utf-8")
        assert [d.document_id for d in load_corpus(tmp_path)] == ["nested/deep.md"]

    def test_it_ignores_files_it_cannot_read_as_text(self, tmp_path: Path) -> None:
        (tmp_path / "keep.md").write_text("text", encoding="utf-8")
        (tmp_path / "skip.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "skip.json").write_text("{}", encoding="utf-8")
        assert [d.document_id for d in load_corpus(tmp_path)] == ["keep.md"]

    def test_it_skips_an_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "empty.md").write_text("   \n\n", encoding="utf-8")
        assert load_corpus(tmp_path) == []

    def test_a_missing_directory_fails_loudly(self, tmp_path: Path) -> None:
        """A wrong corpus path is a config error, not an empty index to find later."""
        with pytest.raises(FileNotFoundError, match="corpus directory does not exist"):
            load_corpus(tmp_path / "nope")


class TestChunkDocuments:
    def test_a_short_document_is_one_chunk(self) -> None:
        document = SourceDocument(document_id="a.md", source="a.md", text="A short note.")
        chunks = chunk_documents([document], chunk_size=512, chunk_overlap=64)
        assert len(chunks) == 1
        assert chunks[0][1] == 0
        assert "A short note." in chunks[0][2]

    def test_a_long_document_splits_into_several_positioned_chunks(self) -> None:
        text = "\n\n".join(f"Paragraph {i} about deployment practice." for i in range(200))
        document = SourceDocument(document_id="long.md", source="long.md", text=text)

        chunks = chunk_documents([document], chunk_size=64, chunk_overlap=8)

        assert len(chunks) > 1
        assert [position for _, position, _ in chunks] == list(range(len(chunks)))

    def test_every_chunk_keeps_its_source_document(self) -> None:
        documents = [
            SourceDocument(document_id="a.md", source="a.md", text="alpha " * 200),
            SourceDocument(document_id="b.md", source="b.md", text="beta " * 200),
        ]
        chunks = chunk_documents(documents, chunk_size=32, chunk_overlap=4)
        assert {d.document_id for d, _, _ in chunks} == {"a.md", "b.md"}


class TestIngestCorpus:
    async def test_it_embeds_and_stores_every_chunk(self) -> None:
        store = InMemoryVectorStore([])
        documents = load_corpus(_CORPUS)

        report = await ingest_corpus(
            documents, embeddings=HashingEmbeddingsClient(dimensions=64), store=store
        )

        assert report.documents == 4
        assert report.chunks == len(store.upserted) > 0
        assert store.collections_ensured == 1

    async def test_chunk_ids_are_deterministic_so_re_ingesting_updates_in_place(self) -> None:
        """Not merely stable-looking: run the whole pipeline twice and compare."""
        documents = load_corpus(_CORPUS)
        first, second = InMemoryVectorStore([]), InMemoryVectorStore([])

        await ingest_corpus(documents, embeddings=HashingEmbeddingsClient(64), store=first)
        await ingest_corpus(documents, embeddings=HashingEmbeddingsClient(64), store=second)

        assert [c.id for c in first.upserted] == [c.id for c in second.upserted]
        assert [c.embedding for c in first.upserted] == [c.embedding for c in second.upserted]

    async def test_chunk_ids_are_document_id_and_position(self) -> None:
        document = SourceDocument(document_id="a.md", source="a.md", text="one note")
        store = InMemoryVectorStore([])

        await ingest_corpus([document], embeddings=HashingEmbeddingsClient(64), store=store)

        assert [c.id for c in store.upserted] == ["a.md:0"]

    async def test_every_stored_chunk_carries_its_provenance(self) -> None:
        """A chunk that cannot say where it came from cannot be cited."""
        store = InMemoryVectorStore([])
        await ingest_corpus(
            load_corpus(_CORPUS), embeddings=HashingEmbeddingsClient(64), store=store
        )
        assert all(c.document_id and c.source for c in store.upserted)
        assert all(len(c.embedding) == 64 for c in store.upserted)

    async def test_ingesting_nothing_does_not_touch_the_store(self) -> None:
        store = InMemoryVectorStore([])
        report = await ingest_corpus([], embeddings=HashingEmbeddingsClient(64), store=store)
        assert report == report.__class__(documents=0, chunks=0)
        assert store.upserted == []
        assert store.collections_ensured == 0
