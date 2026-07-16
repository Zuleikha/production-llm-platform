# data/ — the ingestible document corpus

> **`data/corpus/` is the RAG corpus, not application data.** Every `.md` and
> `.txt` file under it is chunked, embedded and written to Qdrant by
> `scripts/ingest.py`, and the agent's `document_search` tool can return any of
> it to the model.
>
> This README lives in `data/`, one level *above* `data/corpus/`, on purpose:
> `load_corpus` reads everything under the corpus directory, so a README kept
> inside it would be ingested and retrieved as though it were reference
> material. Notes about the corpus are not the corpus.

## What belongs here

Generic engineering reference material — deployment practice, incident
response, observability, code review. Deliberately **domain-agnostic**: this is
scaffolding to exercise retrieval, not a named product's documentation.

Only `.md` and `.txt` files are read, recursively. Anything else is ignored
rather than guessed at.

## Read this before adding a document

Retrieved text goes into the model's context, and the agent is instructed to
treat it as reference data rather than instructions — but that instruction is
mitigation, not immunity (ADR 0014). **Anything written here can influence what
the agent says.** Two consequences:

- Do not put secrets, credentials or personal data in a document here. Chunks
  are stored in Qdrant and returned to clients as citation text.
- Do not add a document containing prompt-injection payloads "to test it". The
  corpus is ingested in `dev` and `prod` too, where it would be a live attack on
  the running agent. Injection test payloads live in the test suite
  (`tests/unit/test_retrieval_tool.py`), which never touches this directory.

Widening what can enter this corpus — user uploads, a web crawl, a third-party
feed — changes the threat model. That is a decision for a new ADR, not a commit.

## Ingesting

```bash
# Costs money under dev/prod: embedding calls the Voyage API (ADR 0011).
uv run python scripts/ingest.py

# Free: the test profile embeds with a deterministic offline hash.
ENVIRONMENT=test QDRANT_URL=http://localhost:6333 uv run python scripts/ingest.py
```

Ingestion is idempotent for unchanged documents — chunk ids are deterministic,
so re-running updates points in place rather than duplicating them.
