"""Ingest a document corpus into Qdrant.

    uv run python scripts/ingest.py                 # ingest data/corpus
    uv run python scripts/ingest.py path/to/docs    # ingest somewhere else

**This costs money under `dev` and `prod`.** Embedding calls the Voyage AI API
and is billed per token (ADR 0011). Under the `test` profile it does not: the
offline hashing double is used instead, so the same command is safe to run
against a scratch Qdrant while developing.

Ingestion is idempotent — chunk ids are deterministic, so re-running against an
unchanged corpus updates each point in place rather than duplicating it. Editing
a document *does* leave the old tail chunks behind if it gets shorter, which is
a known limitation (see the stage 4 summary).

Deliberately a script rather than a service startup hook: ingestion is an
operator action with a bill attached, and a service that silently re-embedded a
corpus on every boot would be a surprising way to spend money.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# scripts/ is not a package; make the repo root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.retrieval.embeddings import build_embeddings_client
from services.retrieval.ingest import ingest_corpus, load_corpus
from services.retrieval.store import QdrantVectorStore
from shared.config import get_settings
from shared.logging import setup_logging

_DEFAULT_CORPUS = Path(__file__).resolve().parent.parent / "data" / "corpus"


async def _run(corpus: Path) -> int:
    settings = get_settings()
    setup_logging(settings)

    if not settings.qdrant_url:
        print("QDRANT_URL is not set — nothing to ingest into.", file=sys.stderr)
        return 2

    documents = load_corpus(corpus)
    if not documents:
        print(f"no .md or .txt documents found under {corpus}", file=sys.stderr)
        return 2

    embeddings = build_embeddings_client(settings)
    # Imported here rather than at module scope: this is the one place a script
    # needs a raw client, and the app gets its own from the datastore registry.
    from qdrant_client import AsyncQdrantClient

    client = AsyncQdrantClient(url=settings.qdrant_url, timeout=60)
    try:
        store = QdrantVectorStore(
            client,
            collection=settings.qdrant_collection,
            dimensions=embeddings.dimensions,
        )
        report = await ingest_corpus(
            documents,
            embeddings=embeddings,
            store=store,
            chunk_size=settings.chunk_size_tokens,
            chunk_overlap=settings.chunk_overlap_tokens,
        )
    finally:
        await client.close()

    print(
        f"ingested {report.chunks} chunks from {report.documents} documents "
        f"into '{settings.qdrant_collection}' "
        f"(profile={settings.environment}, model={settings.voyage_model})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "corpus",
        nargs="?",
        default=_DEFAULT_CORPUS,
        type=Path,
        help=f"directory of .md/.txt documents to ingest (default: {_DEFAULT_CORPUS})",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.corpus))


if __name__ == "__main__":
    raise SystemExit(main())
