# ADR 0015 — An opt-in live contract test for both external providers

- **Status:** Accepted
- **Date:** 2026-07-16
- **Stage:** 4 (RAG)
- **Closes:** the gap ADR 0009 named and Stage 3 left open.

## Context

The hermetic suite substitutes the network with doubles — `ScriptedLLMClient` for
Anthropic (ADR 0009), `HashingEmbeddingsClient` for Voyage (ADR 0011). Those
doubles encode *our beliefs* about each provider's API: field names, event types,
`stop_reason` values, the shape of an embeddings response, the number of floats a
model returns. If a belief is wrong, the fake and the code are wrong **together**
and every hermetic test still passes.

ADR 0009 named this precisely — "the hermetic suite cannot catch a wrong
assumption about the real API" — and Stage 3 shipped with no test against the
live Anthropic API, leaving CI able to miss a provider-side rename entirely.
Stage 4 adds a *second* unverified external surface (Voyage) on top of that gap.
The stage brief is to close both.

## Decision

Add one opt-in, manually-triggered test class,
`test_integration_retrieval.py::TestLiveProviderContract`, that makes **one real
call to each provider** and asserts the response shape the code depends on.

### Double opt-in: a flag *and* the keys

It is skipped unless `RUN_LIVE_CONTRACT_TESTS=1` **and** both `ANTHROPIC_API_KEY`
and `VOYAGE_API_KEY` are set. A key alone is not consent — a developer has one
exported all the time — so an explicit flag is required on top. This is stricter
than the live-datastore tests (which gate on a URL alone), because these calls
cost money where a datastore ping does not.

It builds its clients from a **non-`test` profile on purpose**: the whole point
of the ADR 0009/0011 guards is that the `test` profile *cannot* construct a real
client, so the one place that deliberately makes real calls has to step around
them explicitly.

### What each call asserts

- **Anthropic**: a real `AnthropicClient.stream` yields text deltas and exactly
  one `TurnCompleted`; the completed turn has non-empty text, `stop_reason ==
  "end_turn"`, positive `input_tokens` and `output_tokens` (the fields the usage
  accounting reads), and replayable `raw_content`.
- **Voyage**: `embed_documents` returns one vector per input in order;
  `embed_query` returns exactly `voyage_embedding_dimensions` floats — the
  assertion that cannot be made any other way, because that number is baked into
  the Qdrant collection's vector size at creation (ADR 0012), so a wrong belief
  is a production write failure; and a query embeds closer to a related passage
  than an unrelated one, exercising the asymmetry the hermetic double cannot
  reproduce.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **Run it in CI** | Costs money on every run, needs real keys in CI, and is flaky against live services — everything ADR 0009 rejected for the main suite. |
| **Gate on the keys alone** (like the datastore tests) | A key is present by default on a dev machine; that is not consent to spend money. Hence the extra flag. |
| **Record/replay cassettes** | Requires real calls to record and rots silently against a changing API — passing tests that describe last year's API. |
| **No live test** (the Stage 3 status quo) | Leaves both external surfaces unverified; the explicit thing this stage closes. |

## Consequences

**Positive**

- A provider-side rename of a field the code reads is now catchable by a command a
  human runs deliberately, for both vendors.
- CI stays hermetic, free and key-free.

**Negative / accepted trade-offs**

- **It only runs when someone runs it.** It is a tool for the human changing
  `llm.py` or `embeddings.py`, not an automated guard — which is the correct
  trade for a test that costs money.
- **It makes two billable calls** when invoked. Tiny (see the stage summary for
  the measured cost), but non-zero, and never made without the double opt-in.
