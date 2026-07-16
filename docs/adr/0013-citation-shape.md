# ADR 0013 — Citations are a new top-level response field of typed provenance

- **Status:** Accepted
- **Date:** 2026-07-16
- **Stage:** 4 (RAG)

## Context

A grounded answer is only trustworthy if a client can trace it back to the
sources it was built from. Stage 4 retrieves document chunks and feeds them to
the model; the response has to surface *which* chunks, in a way a client can act
on — resolve to a document, deduplicate, highlight, or show the user.

Three things had to be decided: **where** citations go in the wire format, **what
shape** each one has, and **how** provenance travels from the retrieval tool to
the response without being corrupted on the way.

## Decision

### A new top-level `citations` field, not an overload of an existing one

`ChatCompletionResponse` gains a top-level `citations: list[CitationModel]`, and
the streamed `ChatCompletionChunk` gains `citations: list[CitationModel] | None`.
It is not folded into `message.content` (which is the answer text) or `usage`
(which is token accounting). Overloading either would force a client to parse
prose, or to read something out of a field that means something else, to find out
what grounded an answer.

Each citation is:

```
{ id, document_id, source, score, text }
```

`id` and `document_id` differ on purpose: `id` is the exact chunk that matched
(`deployments.md:1`), `document_id` the document it came from (`deployments.md`).
A client resolving a citation to something a human can open wants the second; one
deduplicating or highlighting wants the first. `score` lets a client rank or
threshold; `text` is the excerpt itself, so a citation can be shown without a
second fetch.

### Empty means "nothing grounded this", and is reported, not omitted

An answer the agent produced without retrieving reports `"citations": []`, not a
missing field. "This answer is not grounded in any source" is a fact a client
should be able to see, and the difference between an ungrounded answer and a
grounded one is exactly what the field exists to make visible.

### Streaming: citations on the final frame only

Citations are known only once the run is complete — an agent may search again
*after* emitting its first answer text — so they cannot be accumulated frame by
frame. The streamed frames carry `citations` only on the final chunk (the one
with `finish_reason`); every text frame omits the field entirely (via
`exclude_none`). A client reads them from the final frame. The streamed and
whole-response paths report identical citations, proven by a test — streaming is
a transport choice and must not change provenance.

### Provenance is typed data carried out-of-band, never parsed from text

This is the load-bearing decision, and it is a security decision. The text the
model reads is fenced, untrusted document content (ADR 0014). Citations are
carried as typed `Citation` objects **alongside** that text, from the tool
(`ToolResult.citations`) through graph state (a reduced `citations` channel),
the orchestrator, and the engine, to the wire. They are **never** rebuilt by
parsing the excerpt text the model saw.

If provenance were parsed back out of the rendered text, a document could forge
its own citation — write `source="handbook.md"` in its body and be cited as
`handbook.md`. Because the citation comes from the retriever's own record of what
it returned, document content cannot forge it. A test pins exactly this: a
document claiming to be `handbook.md` in its text is still cited as its true
source.

Citations accumulate across a run, deduplicated by chunk id (first occurrence
wins), so an agent that finds the same chunk in two searches cites it once.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **Inline markers in `message.content`** (`[1]`, `[deployments.md]`) | Forces the client to parse prose; the markers are model-authored and unreliable; couples provenance to answer text. |
| **A field inside `usage`** | `usage` is token accounting; citations are not usage. |
| **Rebuild citations from the fenced excerpt text** | Lets a document forge its own provenance — the exact thing ADR 0014 fences against. |
| **Accumulate citations across streamed frames** | Wrong: an agent can retrieve after its first answer text, so early frames cannot know the final set. |
| **Omit the field when empty** | Hides the difference between "ungrounded" and "grounded", which is what the field is for. |

## Consequences

**Positive**

- A client can trace any grounded answer to source chunks, and tell a grounded
  answer from an ungrounded one, without parsing prose.
- Provenance cannot be forged by document content.
- Streamed and non-streamed responses agree on citations by construction.

**Negative / accepted trade-offs**

- **The wire format now diverges from OpenAI's** by one field. Deliberate: a
  client that ignores `citations` sees exactly the Stage 3 shape, and one that
  wants provenance has a first-class place to read it.
- **`text` duplicates the excerpt** already present (fenced) in the model's
  context earlier in the run. Accepted: the client never sees that context, so
  the citation has to carry its own excerpt.
