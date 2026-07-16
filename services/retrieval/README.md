# retrieval

> **Status: implemented in Stage 4 (RAG).**

Grounds the agent's answers in retrieved documents, with citations. Ingestion,
embeddings, vector search and the agent's retrieval tool.

## Contract

| Type | Kind | Meaning |
|------|------|---------|
| `DocumentChunk` | dataclass | What you *put in*: text + embedding + provenance. |
| `RetrievedDocument` | dataclass | What you *get out*: a chunk + a relevance score. |
| `Retriever` | Protocol | `async retrieve(query, top_k) -> Sequence[RetrievedDocument]`. |
| `VectorStore` | ABC | `ensure_collection()` / `upsert(chunks)` / `query(embedding, top_k)`. |
| `EmbeddingsClient` | Protocol | `embed_documents` / `embed_query`, asymmetric. |

`DocumentChunk` and `RetrievedDocument` are two types, not one — a correction to
the Stage 1 stub, which had `upsert` take the *result* type. Storing and
retrieving are different shapes: you cannot upsert a relevance score, and a query
does not carry an embedding. See `base.py`.

## Modules

| Module | Does |
|--------|------|
| `embeddings.py` | Voyage client + offline `HashingEmbeddingsClient` double + `build_embeddings_client` seam. |
| `store.py` | `QdrantVectorStore` on `qdrant-client`. |
| `ingest.py` | `load_corpus` → `chunk_documents` (LlamaIndex) → embed → `upsert`. |
| `retriever.py` | `VectorRetriever` (embed query, search, drop weak matches) + `build_retriever`. |
| `tool.py` | `DocumentSearch` — the agent's retrieval tool. **The injection boundary.** |

## Flow

```
ingest:   data/corpus/*.md → chunk → embed (Voyage) → Qdrant
retrieve: query → embed (Voyage) → Qdrant search → drop < min_score
answer:   agent calls document_search → fenced excerpts → model → answer + citations
```

## Two things to read before extending

- **`tool.py` is a prompt-injection boundary (ADR 0014).** Retrieved text is
  attacker-influenceable and goes into the model's context. It is fenced with a
  per-call nonce and labelled untrusted; citations are typed data, never parsed
  from text. Delimiting is not immunity. Widening what enters the corpus changes
  the threat model — revisit the ADR.
- **The `test` profile cannot construct a real Voyage client (ADR 0011),** the
  same guard as Anthropic (ADR 0009). Embeddings go through
  `build_embeddings_client`; under `test` that is a deterministic offline hash,
  by construction, not by convention.

See ADR 0011 (embeddings), 0012 (Qdrant), 0013 (citations), 0014 (injection),
0015 (live contract test).
