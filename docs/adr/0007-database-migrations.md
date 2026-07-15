# ADR 0007 — Database migrations: raw SQL, forward-only

- **Status:** Accepted
- **Date:** 2026-07-15
- **Stage:** 3 (Agents)

## Context

Stage 3 introduces the project's first schema: conversations and their messages.
Stage 2 connected Postgres but stored nothing, so there has never been a
migration story. There is now.

Two facts constrain the choice:

- **The codebase has no ORM.** Every query is raw `asyncpg` (ADR 0005 explicitly
  rejected SQLAlchemy for the same reason: it drags an ORM in to satisfy a
  `SELECT 1`).
- **The API boots in containers, possibly several at once.** Whatever applies
  migrations must be safe to run concurrently and idempotent.

## Decision

**Plain `.sql` files in `migrations/`, applied by a ~100-line runner in
`shared/migrations.py`, from the application lifespan.**

- **Named `NNNN_lower_snake_name.sql`**, applied in numeric order. A misnamed or
  duplicate-versioned file raises rather than being skipped — a migration
  silently ignored because of a typo is schema drift discovered in production.
- **Recorded in `schema_migrations`.** A version already recorded is skipped, so
  every boot is idempotent.
- **Each file commits with its bookkeeping row** in one transaction. A crash
  mid-file cannot leave a version recorded but unapplied.
- **Guarded by a session-level advisory lock** (`pg_advisory_lock`). Two API
  containers booting together cannot race to apply `0001`; the second blocks,
  then finds nothing pending. Released in a `finally` — a stuck advisory lock
  would wedge every subsequent boot.
- **Applied in the lifespan, after `datastores.startup()`**, and — like
  `startup()` — **it never raises** (ADR 0005). A migration that cannot run
  surfaces as a logged error and an un-ready pod, not a crash loop that hides
  why.
- **Forward-only.** There is no `downgrade`.

### Why no framework

Alembic is the obvious candidate and it is a good tool. It is also
SQLAlchemy's migration tool: adopting it means adding SQLAlchemy to a codebase
that deliberately has no ORM, and then owning two incompatible ways to talk to
Postgres — `asyncpg` in the application, SQLAlchemy in the migrations. Its
autogenerate feature, the main reason to pay that cost, works by diffing ORM
models we do not have.

What Alembic would buy us over the runner above is branching/merge resolution and
a downgrade path. We have one linear history and no downgrades. Revisit if either
of those stops being true — the SQL files port to any runner, which is precisely
why they are plain SQL.

### Why forward-only

A `downgrade` has to be written before the failure it handles is understood, and
it is exercised approximately never — so it is a second untested code path that
runs only during an incident. Rolling forward with a new migration, or restoring
from a backup, are both things we would actually test. This is a real trade-off,
not a free win: it means a bad migration needs a fix-forward under pressure.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **Alembic** | Drags SQLAlchemy into an ORM-free codebase; autogenerate needs models we don't have; buys branching and downgrades we don't use. |
| **yoyo-migrations / dbmate** | Closer fit (raw SQL, no ORM), but still a dependency and a CLI in the deploy path to satisfy what a page of code does. Reconsider if the history stops being linear. |
| **Apply migrations from a separate job / init container** | The right answer at scale, and where this goes if it grows. Today it means a Compose/K8s change per environment to create two tables, and Stage 7 owns that surface. The advisory lock makes lifespan application safe in the meantime. |
| **`CREATE TABLE IF NOT EXISTS` on boot, no versioning** | Works exactly once. There is no way to alter a column later, and no record of what has been applied. |
| **An ORM's `create_all()`** | Same ORM objection, plus it silently does nothing on schema changes. |
| **Raise on migration failure** | Crash loop instead of a diagnosable un-ready pod — the failure mode ADR 0005 exists to avoid. |
| **Downgrade scripts** | Untested code that runs only during incidents. |
| **No advisory lock** | Two replicas booting together race to apply the same file. Postgres would mostly save us via transactions, but "mostly" is not a concurrency design. |

## Consequences

**Positive**

- One way to talk to Postgres, everywhere.
- Migrations are readable SQL — reviewable by anyone, portable to any runner.
- Safe on every boot and from every replica.
- No new dependency.

**Negative / accepted trade-offs**

- **No downgrades.** A bad migration is fixed forward or restored from backup.
- **No branching support.** A linear history is assumed; two developers adding
  `0002` concurrently get a loud duplicate-version error and resolve it by hand.
- **Migrations run in the API's lifespan**, so schema changes are coupled to
  deploys and a large migration would delay readiness. Fine for two small tables;
  revisit at Stage 7 when there is a job runner.
- **A failed migration leaves the service running but broken** — it will fail its
  queries loudly on the first request. Deliberate (see above), but it does mean
  `/ready` currently reports `ok` for a service whose schema never applied. The
  honest fix is a readiness check on schema version; deferred, and listed as a
  known limitation in the Stage 3 summary.
- **The runner is ours to maintain.** It is small and tested, but it is code a
  library would otherwise own.
