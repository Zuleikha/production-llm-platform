# ADR 0012 — Qdrant collection design and the switch to `qdrant-client`

- **Status:** Accepted
- **Date:** 2026-07-16
- **Stage:** 4 (RAG)
- **Extends:** ADR 0005 (datastore connection pooling), which anticipated this
  swap.

## Context

Qdrant has been connected and probed since Stage 2, but holding no data. Stage 2
reached it with a raw pooled `httpx.AsyncClient` hitting `/readyz`, because a
readiness probe was all it needed and `qdrant-client` was a Stage 4 dependency
(ADR 0005 explicitly deferred it). Stage 4 is the stage that puts vectors in the
collection, which forces three decisions: the client, the collection schema, and
the point-id scheme.

## Decision

### Swap the raw-httpx probe for `qdrant-client`

`QdrantDatastore` now wraps `AsyncQdrantClient` (`qdrant-client==1.18.0`).
Hand-rolling upsert and vector search over raw HTTP would be reimplementing the
client, badly. The `Datastore` contract is unchanged — `configured` / `connect` /
`close` / `ping` — so nothing above it moved; the registry exposes the live
client through a new `qdrant_client` property, mirroring `postgres_pool` and
`redis_client`.

The probe changes from `GET /readyz` to `get_collections()`. That is a slightly
*stronger* check: it proves the API answers queries, not merely that the process
is up.

The compose server image is bumped from `v1.12.4` to `v1.18.1` to match the
client's minor version — the client warns and may misbehave beyond a one-minor
gap. Stage 2 could pin the server freely because it spoke raw HTTP; from Stage 4
client and server move together.

### Collection schema: one collection, cosine distance, fixed dimension

- **One collection** (`QDRANT_COLLECTION`, default `documents`), holding every
  chunk of every document. The corpus is small and homogeneous; per-document or
  per-source collections would buy nothing and complicate search.
- **Cosine distance.** The embeddings are L2-normalised and what matters is
  direction (topical similarity), not magnitude.
- **Vector size taken from the embeddings client**, not a second setting.
  `build_retriever` reads `embeddings.dimensions` and passes it to the store, so
  the collection's fixed vector size cannot silently disagree with the model that
  produces the vectors.
- **Payload** carries `chunk_id`, `text`, `document_id`, `source`, `position` —
  enough to rebuild a `RetrievedDocument` and therefore a citation, with no
  second lookup.

### Point ids are deterministic UUIDv5, derived from the chunk id

`PointStruct.id` is typed `int | str | UUID`, which reads as "any string will
do". It will not: the Qdrant **server** rejects an arbitrary string such as
`deployments.md:3`. Chunk ids are mapped onto a UUIDv5 under a fixed namespace
(`point_id_for`). Deterministic, so re-ingesting the same chunk updates its point
in place rather than duplicating it — which is what makes ingestion idempotent.
`test_integration_retrieval.py::TestAgainstRealQdrant::test_qdrant_accepts_our_point_ids`
is the test that would have caught this against a real server.

### `ensure_collection` never recreates

Creating the collection is idempotent and separate from `upsert`, and it does not
recreate an existing collection — that would silently delete every stored vector.
A collection that exists with the wrong dimension surfaces as a write failure,
which is the louder and more recoverable outcome.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **Keep raw httpx and hand-roll search** | Reimplements `qdrant-client` badly; ADR 0005 already planned this swap. |
| **A collection per document or per source** | No benefit for a small homogeneous corpus; more collections to create, probe and search. |
| **A second `QDRANT_DIMENSIONS` setting** | Can drift from the model's real output; deriving it from the client makes disagreement impossible. |
| **Raw chunk-id strings as point ids** | Rejected by the Qdrant server at write time. |
| **Recreate the collection on ensure** | Silently destroys stored vectors on every boot. |
| **Store only ids, look text up elsewhere** | A second store to keep consistent; the payload already carries what a citation needs. |

## Consequences

**Positive**

- Real vector search behind the unchanged `Datastore` contract; a stronger
  readiness probe than Stage 2's.
- Idempotent ingestion, provable against a live Qdrant.
- Client and server versions pinned in lockstep.

**Negative / accepted trade-offs**

- **Editing a document that gets shorter leaves its old tail chunks orphaned.**
  Deterministic ids update existing positions in place, but a document that drops
  from 5 chunks to 3 leaves chunks `:3` and `:4` behind. Ingestion is
  upsert-only; there is no delete-then-write. Fine for an append-mostly corpus,
  documented as a limitation, and a per-document purge-before-write is the fix
  when it matters.
- **`qdrant-client` pulls a sizeable transitive tree** (grpcio, protobuf) into
  the base image. Accepted as the cost of not reimplementing it.
