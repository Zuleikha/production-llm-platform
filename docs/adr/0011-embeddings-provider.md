# ADR 0011 — Embeddings via Voyage AI, behind a profile-keyed hermetic seam

- **Status:** Accepted
- **Date:** 2026-07-16
- **Stage:** 4 (RAG)

## Context

RAG needs an embeddings model: something that turns a chunk of text and a query
into vectors whose cosine similarity tracks relevance. Two questions had to be
settled before any retrieval code could be written — *which provider*, and *how
the test suite avoids paying it*.

**Anthropic has no embeddings API.** The platform's chat backend is Claude, so
the obvious wish is one vendor. It does not exist: Anthropic's public API is
Messages, Batches, Files, Token Counting and Models — there is no embeddings
endpoint. Anthropic's own RAG documentation points at **Voyage AI** as the
recommended pairing. So a second paid vendor is unavoidable, and the honest thing
is to make it a first-class, guarded dependency rather than pretend otherwise.

The second question is the one Stage 3 already answered for Anthropic (ADR 0009):
a test suite that drives the retrieval path must not be able to bill Voyage, and
"we mock it in tests" is not good enough because a developer's machine has
`VOYAGE_API_KEY` exported and would behave — and bill — differently from CI.

## Decision

### Voyage AI, model `voyage-3.5-lite`, 1024 dimensions

`voyageai==0.5.0`, promoted to a base dependency. The model is a setting
(`VOYAGE_MODEL`, default `voyage-3.5-lite`); the dimensionality is a setting too
(`VOYAGE_EMBEDDING_DIMENSIONS`, default 1024), because it is also the Qdrant
collection's vector size and the two must agree (ADR 0012).

`voyage-3.5-lite` is the small, cheap tier of the current Voyage generation — the
right default for a demonstration corpus where retrieval quality is more than
adequate and per-token cost is the thing worth minimising. A larger model is a
one-line settings change if a real corpus ever needs it.

### `llama-index-core`, not the `llama-index` meta-package

Ingestion uses LlamaIndex's `Document` and `SentenceSplitter` and nothing else.
The `llama-index` meta-package depends on `llama-index-llms-openai` and
`llama-index-embeddings-openai` — it would install the OpenAI SDK into the base
image of an Anthropic-plus-Voyage platform. `llama-index-core` has the ingestion
primitives and none of that. This is the same call, on the same grounds, as
dropping `langchain` rather than promoting it in Stage 3 (ADR 0006).

### The `test` profile cannot construct a real Voyage client

Identical in shape to the Anthropic guard (ADR 0009), keyed on the **profile**,
not the key's presence. Two independent mechanisms:

1. **`build_embeddings_client(settings)`** returns `HashingEmbeddingsClient`
   under `test` and never reaches the real constructor.
2. **`VoyageEmbeddingsClient.__init__` raises under `test`**, before the key is
   read. This is the load-bearing one: no import order, fixture order or
   monkeypatch turns a unit test into a paid call, including code that reaches
   past the factory.

`test_embeddings.py::TestTestProfileCannotCallVoyage` pins the mechanism,
including the case that distinguishes hermetic from merely-unconfigured: a real
key exported in the OS environment does not change either behaviour.

### The double produces real cosine similarity

`HashingEmbeddingsClient` hashes tokens into an L2-normalised bag-of-words
vector, so lexical overlap genuinely drives the score. That is what lets the
integration test assert that "how do I roll back a deployment" retrieves the
rollback document *because retrieval worked*, not because a mock was told to
return it. It understands no meaning and makes no network call — both are the
point. It is real code living in application space, for the same reason
`ScriptedLLMClient` is (ADR 0009).

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **A local model (FastEmbed / sentence-transformers)** | Free and offline, but collapses the design tension the stage exists to exercise: no external client to guard, so the hermetic seam is a no-op and the live contract test has nothing to call. Also adds model weights to the image. |
| **OpenAI `text-embedding-3`** | Works, but introduces a second AI vendor into a codebase that is otherwise deliberately Anthropic-only, with no advantage over Voyage, which Anthropic actually recommends. |
| **The `llama-index` meta-package** | Drags the OpenAI SDK into the base image (see above). |
| **Guard the embeddings client on "no key set"** | The developer/CI split ADR 0009 rejects: the suite behaves differently, and bills, exactly where the code is written. |
| **Mock Voyage per test** | Opt-in; one forgotten patch is a charge, not a red test. |

## Consequences

**Positive**

- CI needs no `VOYAGE_API_KEY` and cannot spend money embedding. The suite is
  identical with a key exported and without one.
- The retrieval path — ingestion, the retriever, the tool — is exercised for real
  against a deterministic embedder; only the network is substituted.
- The provider and model are one settings change away from a larger tier.

**Negative / accepted trade-offs**

- **A second paid vendor**, with its own key, its own boot requirement under
  `prod` (extended the ADR 0005/0006 validator), and its own contract-test blind
  spot (ADR 0015).
- **The double lives in application code** and ships in the image — a few hundred
  bytes, never constructed outside `test`.
- **The double does not reproduce Voyage's asymmetry** (document vs query
  embeddings). It embeds both identically, because faking a trained model's
  asymmetry with a hash would be a fake that lies about the real one. The
  asymmetry is therefore only exercised by the live contract test.
