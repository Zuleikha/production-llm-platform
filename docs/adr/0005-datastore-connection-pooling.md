# ADR 0005 — Datastore connection pooling and readiness

- **Status:** Accepted
- **Date:** 2026-07-14
- **Stage:** 2 (API)

## Context

Stage 1 ran Postgres, Redis and Qdrant in Compose but connected to none of them:
`/ready` returned `checks: {}` unconditionally, which is a lie an orchestrator
will happily act on. Stage 2 has to open real connections and make `/ready`
mean something.

Three forces shape this:

- **Connections are expensive.** A TCP+TLS+auth handshake per request is the
  classic way to make a fast service slow and to exhaust a database's connection
  limit under load.
- **The test suite must stay hermetic.** Unit tests cannot require a running
  Postgres.
- **Liveness and readiness answer different questions.** Conflating them turns a
  brief database blip into a pod restart storm.

## Decision

### Pool per store, opened once in the lifespan

Every datastore is wrapped in a `Datastore` (`shared/datastores.py`) owning a
**pool**, not a connection, behind a uniform contract: `configured`, `connect`,
`close`, `ping`. A `DatastoreRegistry` fans lifecycle and probes across all
three. Pools open in `create_app`'s lifespan startup and close on shutdown; pool
sizes and timeouts are `Settings` fields, not literals.

Each store uses its driver's native pool rather than a hand-rolled one:
`asyncpg.create_pool`, `redis.asyncio`'s built-in pool, and `httpx.AsyncClient`
(which pools by default via its connection limits).

### Qdrant over HTTP, not `qdrant-client`

Qdrant is reached with a pooled `httpx.AsyncClient` against `GET /readyz`.

`qdrant-client==1.18.0` is pinned in the `retrieval` extra and belongs to
**Stage 4**. Stage 2 needs a readiness probe and a pooled connection — nothing
that justifies pulling a Stage-4 library into base dependencies and undercutting
the stub rule. Stage 4 can swap it in for real vector operations behind the same
`Datastore` contract.

**This makes `httpx` a runtime dependency, not a test-only one** — it was
previously in the `dev` group for `TestClient`.

### Startup never crashes the process, and never blocks on a dead one

`registry.startup()` catches and logs connection failures rather than
propagating them, recording the store as failed. A datastore that is down at
boot must produce an honest un-ready pod, not a crash loop — crash-looping loses
the logs and the `/ready` signal that would explain *why*, exactly when someone
is trying to diagnose it.

Connections are opened **concurrently** (`asyncio.gather`). Sequentially, three
unreachable stores delay `/health` by the *sum* of their connect timeouts;
measured at ~20s in the container, versus ~7s concurrently. That delay is not
cosmetic — it is long enough for an orchestrator's liveness probe to kill the
pod before it ever serves, reintroducing the crash loop this section exists to
prevent.

A store whose `connect` failed reports `unavailable` without being re-pinged: its
pool was never opened, so a ping cannot be trusted.

### `/health` and `/ready` are genuinely different

- **`/health`** — pure liveness, touches no datastore. It answers "should this
  container be restarted?". A test with a tripwire datastore asserts `/health`
  never calls `ping`.
- **`/ready`** — probes every configured store **concurrently** (`asyncio.gather`,
  each bounded by `datastore_probe_timeout_seconds`) and returns **503** with
  `{"status": "not_ready", "checks": {...}}` if any is `unavailable`. It answers
  "should this container get traffic?".

### `not_configured` is a pass — because prod cannot reach it

A store with no URL reports `not_configured`, is never dialled, and does not
fail readiness. That is what keeps the test profile hermetic: it sets no URLs.

The obvious hole is production — a missing or typo'd `DATABASE_URL` would read
as `not_configured` and **silently pass readiness**, which violates fail-loud.
So the guard sits at the boundary where it belongs: a `Settings` validator
**requires all three URLs under the `prod` profile** and refuses to construct
otherwise. Production fails at boot with a named variable, rather than passing
readiness with no database.

This deliberately puts the check in config rather than in `/ready`: a
misconfigured service should never start, not start and then quietly report
itself un-ready.

### Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| A connection per request | Handshake cost per call; exhausts the server's connection limit under load. The problem pools exist to solve. |
| One global shared connection | No concurrency, and one dropped socket takes down every request. |
| SQLAlchemy async engine | A capable pool, but drags in an ORM and its migration story to satisfy a `SELECT 1`. `asyncpg` is already the pinned driver; revisit when there is a schema. |
| Pull `qdrant-client` into base now | Promotes a Stage-4 dependency to satisfy a health check, and undercuts the stub rule for a `GET /readyz`. |
| Fail startup when a datastore is down | Crash loop instead of a diagnosable un-ready pod; loses exactly the signal an operator needs. |
| Connect sequentially at startup | Liveness is delayed by the sum of every connect timeout (~20s measured, vs ~7s concurrent) — long enough to trip a liveness probe and cause the crash loop we are avoiding. |
| `not_configured` fails readiness in prod | Same protection, later and vaguer — a pod that never goes ready, versus a boot error naming the missing variable. |
| Probe datastores from `/health` too | Turns a database blip into a restart storm, and restarting the API cannot fix a database. |
| Probe sequentially | `/ready` latency becomes the sum of three timeouts; it is polled constantly. |

## Consequences

**Positive**

- Handshakes are paid once at startup, not per request.
- `/ready` reflects reality; Kubernetes (Stage 7) can route on it truthfully.
- The `Datastore` contract means Stage 4 swaps Qdrant's implementation without
  touching `/ready` or the lifespan.
- Unit tests need no live datastore — fakes satisfy the same ABC.
- Base dependencies stay free of Stage-4 libraries.

**Negative / accepted trade-offs**

- **No reconnect after a failed boot.** A store whose `connect` failed stays
  `unavailable` until the process restarts; there is no retry or circuit
  breaker. (A store that connected and *later* blipped does recover, because the
  driver pools reconnect on the next ping — verified by stopping and restarting
  Redis against a live stack.) Acceptable while Kubernetes restarts un-ready
  pods — revisit in Stage 9 (reliability).
- **Pool sizes are guesses.** Defaults (10 connections) are untuned; Stage 9's
  load testing is what should set them.
- **`/ready` can be slow to fail.** It waits up to `datastore_probe_timeout_seconds`
  per hung store, bounded but not instant.
- **`prod` now refuses to boot without all three URLs**, including Qdrant, which
  no code queries until Stage 4. Deliberate: consistent and honest beats a
  special case that erodes.
- Redis's `from_url` is lazy, so `connect` issues an explicit `ping` to force the
  handshake — without it, an unreachable Redis would appear to connect fine.
