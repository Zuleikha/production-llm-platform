# retrieval — interface stub

> **Status: planned, not yet implemented.** Delivered in **Stage 4 (RAG)**.
> Stage 1 ships only the contracts below.

## Intended contract

| Type | Kind | Meaning |
|------|------|---------|
| `RetrievedDocument` | dataclass | `id`, `text`, `score` for one result. |
| `Retriever` | Protocol | `async retrieve(query, top_k) -> Sequence[RetrievedDocument]`. |
| `VectorStore` | ABC | `upsert(...)` / `query(embedding, top_k)` backing store. |

## Planned scope (Stage 4)

- Document ingestion & chunking via **LlamaIndex**.
- Embeddings + similarity search backed by **Qdrant** (already wired in
  `docker-compose.yml`).
- Grounded RAG responses with citation of `RetrievedDocument.id`.

All abstract methods currently raise `NotImplementedError`.
