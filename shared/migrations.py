"""A minimal forward-only migration runner over raw SQL files.

The project talks to Postgres through raw ``asyncpg`` and owns no ORM. Adopting
Alembic to create two tables would drag SQLAlchemy in behind it and give the
codebase a second, incompatible way to reach the database. So migrations are
what the rest of the code already is: SQL, applied in order, recorded in a
table. See ADR 0007 for the alternatives weighed.

Rules this enforces:

- **Ordered.** Files are ``NNNN_name.sql`` and apply in numeric order.
- **Once.** Applied versions are recorded in ``schema_migrations``; a version
  already recorded is skipped.
- **Atomic per file.** Each migration runs inside a transaction with its
  bookkeeping row, so a failure leaves no half-applied version behind.
- **Serialised across replicas.** A session-level advisory lock means two API
  containers booting together cannot race to apply the same file.

There is deliberately no ``downgrade``. A rollback that has to be written before
the failure is understood is a second untested code path; forward-only plus a
restore is the honest posture at this stage.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple

from shared.logging import get_logger
from shared.observability import traced

if TYPE_CHECKING:
    import asyncpg

_logger = get_logger("shared.migrations")

_REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR: Final[Path] = _REPO_ROOT / "migrations"

_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

# An arbitrary but fixed key: advisory locks are global to the database, so this
# number just has to be stable and not collide with another user of the lock
# space. Derived from nothing — it only needs to be constant.
_ADVISORY_LOCK_KEY: Final[int] = 0x6C6C6D70  # "llmp"

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER     PRIMARY KEY,
    name        TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class Migration(NamedTuple):
    """One migration file on disk."""

    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


@traced
def discover(directory: Path | None = None) -> list[Migration]:
    """Return every migration file, ordered by version.

    Raises:
        ValueError: on a misnamed file or a duplicate version. Both are loud on
            purpose — a migration silently skipped because it was named wrongly
            is a schema drift you find in production.
    """
    directory = directory or MIGRATIONS_DIR
    if not directory.is_dir():
        return []

    migrations: list[Migration] = []
    seen: dict[int, str] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise ValueError(
                f"migration {path.name!r} is misnamed: expected NNNN_lower_snake_name.sql"
            )
        version = int(match.group("version"))
        if version in seen:
            raise ValueError(
                f"duplicate migration version {version:04d}: {seen[version]!r} and {path.name!r}"
            )
        seen[version] = path.name
        migrations.append(Migration(version=version, name=match.group("name"), path=path))
    return migrations


@traced
async def apply_pending(
    pool: asyncpg.Pool[asyncpg.Record], directory: Path | None = None
) -> list[int]:
    """Apply every migration not yet recorded. Returns the versions applied.

    Safe to call on every boot and from every replica concurrently.
    """
    migrations = discover(directory)
    if not migrations:
        return []

    applied: list[int] = []
    async with pool.acquire() as connection:
        # Session-level lock: two containers booting together must not both try
        # to apply 0001. The second blocks here, then finds nothing pending.
        await connection.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        try:
            await connection.execute(_BOOTSTRAP)
            done = {
                row["version"]
                for row in await connection.fetch("SELECT version FROM schema_migrations")
            }
            for migration in migrations:
                if migration.version in done:
                    continue
                # The migration and its bookkeeping row commit together, so a
                # crash mid-file cannot leave a version recorded but unapplied.
                async with connection.transaction():
                    await connection.execute(migration.sql)
                    await connection.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
                        migration.version,
                        migration.name,
                    )
                applied.append(migration.version)
                _logger.info(
                    "migration.applied",
                    extra={"version": migration.version, "name": migration.name},
                )
        finally:
            await connection.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_KEY)

    if not applied:
        _logger.info("migration.up_to_date", extra={"versions": len(migrations)})
    return applied
