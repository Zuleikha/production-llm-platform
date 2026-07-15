# ADR 0008 — Conversation caching: Redis read-through, Postgres owns the truth

- **Status:** Accepted
- **Date:** 2026-07-15
- **Stage:** 3 (Agents)

## Context

Stage 3 persists conversation history. Every turn of a conversation re-reads all
prior turns to build the model's context, so history is read far more often than
it is written and the read is on the critical path of a request that is already
slow (a model call). Stage 2 connected Redis but never used it.

The tempting mistake is to treat Redis as the store — it is faster, the data is
naturally keyed, and the code would be simpler. Redis is an **eviction-based
cache**: under memory pressure it drops keys, by design. A conversation stored
only in Redis is a conversation that silently disappears.

## Decision

**Read-through cache: `CachedConversationStore` wraps
`PostgresConversationStore`. Postgres is the source of truth; Redis only ever
holds a copy.**

Three rules, and the third is the one that matters.

### 1. Reads try Redis, fall through, repopulate

`load()` reads the cache; on a miss it reads Postgres and populates the entry
with a TTL (`conversation_cache_ttl_seconds`, default 300s).

### 2. A Redis failure costs latency, never correctness

Every Redis call in `load()` is wrapped: a failure is logged and the read falls
through to Postgres. A corrupt or unparseable entry is treated as a miss, not a
500 — someone wrote the key by hand, or the format changed under a running
deploy, and neither is worth failing a request when the truth is one query away.

This is the **one** place in the codebase that deliberately swallows an
exception, against the project's fail-loud rule. It earns the exception because
the cache is an optimisation with a correct fallback one line away. Note
`append()` does *not* swallow its Postgres write — losing the cache is free,
losing the turn is not.

### 3. Writes invalidate; they do not update

`append()` writes through to Postgres and then **deletes** the cache entry.

Rewriting the entry with the new history would avoid the next miss. It would also
mean computing the post-append history in two places — here and in Postgres — and
trusting them to agree. They will agree until the day they don't, and the symptom
is a conversation whose stored history disagrees with what the model was shown.
Deleting cannot disagree with anything. The cost is one Postgres read on the next
turn, against a model call that takes seconds.

**Order matters: Postgres first, invalidate second.** Invalidating first opens a
window where a concurrent read repopulates the cache from the pre-append state
and that stale entry then survives for the whole TTL. A test pins the ordering.

### Redis is never promoted to source of truth

`build_conversation_store` returns `NullConversationStore` when Postgres is
absent — **even if Redis is available**. A cache-only store would look like it
worked right up until the first eviction. No Postgres means no persistence, said
plainly.

### The cache depends on a narrow port, not on Redis

`CachedConversationStore` takes a `CacheBackend` protocol — `get`, `set`,
`delete`. It needs a keyed store with a TTL, not a Redis. This keeps the class
honest about its requirements and makes its failure paths testable without
running a Redis in order to break one.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **Redis as the source of truth** | It evicts under memory pressure. Data loss presented as a cache policy. |
| **Write-through (update the cache on append)** | Computes the same history in two places and hopes they match. Saves one read on a path already dominated by a model call. |
| **Write-behind (queue the Postgres write)** | Acknowledges a turn that may never be durable. Wrong trade for the sake of a write that is already fast. |
| **Invalidate before writing to Postgres** | Leaves a window where a concurrent read re-caches pre-append history for a full TTL. |
| **No TTL (invalidate-only)** | One missed invalidation — a crash between the write and the delete — becomes permanently stale. The TTL bounds the blast radius of our own bugs. |
| **Cache the model's rendered prompt instead of history** | Ties the cache to the prompt format; every prompt change silently invalidates everything and the entries are much larger. |
| **Fail the request when Redis is down** | Turns an optional dependency into a required one. |
| **Depend on `redis.asyncio.Redis` directly** | Forces a live Redis (or heavy patching) to test the degradation paths that matter most. |

## Consequences

**Positive**

- Losing Redis entirely costs latency and nothing else — proven by tests, not
  asserted.
- History cannot silently vanish: Postgres is the only writer of record.
- The store composes — `Null` / `Postgres` / `Postgres + cache` are selected by
  what is actually configured, matching ADR 0005's `not_configured` posture.
- The cache's failure and corruption paths are unit-tested without a Redis.

**Negative / accepted trade-offs**

- **Every write costs a cache miss on the next read.** Deliberate; the alternative
  trades correctness for one query.
- **A crash between the Postgres write and the `DELETE` leaves a stale entry** for
  up to the TTL. Bounded, and the reason the TTL exists at all.
- **The TTL is a guess.** 300s is untuned — no load data exists yet. Stage 9.
- **No per-conversation locking.** Two concurrent turns on the same conversation
  can interleave: both read the same history, and both append. The unique index on
  `(conversation_id, position)` prevents corrupt *ordering*, but the second write
  fails loudly rather than serialising. Acceptable while a conversation is driven
  by one client; a real fix needs a row lock or an optimistic version.
- **The whole history is cached as one value.** A very long conversation makes for
  a large entry and a large read. Fine at current sizes; a windowed cache is the
  answer if it stops being.
- **This is the one sanctioned exception to fail-loud**, and exceptions erode.
  It is confined to `load()`, and the reasoning is written down here and at the
  call site.
