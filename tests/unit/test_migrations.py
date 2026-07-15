"""Tests for the SQL migration runner (ADR 0007).

Discovery is tested against real files; application is tested against a fake
pool, so these prove the runner's ordering, skipping and bookkeeping — not that
Postgres accepts the SQL. The shipped migration is executed against a real
database only by the integration test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from shared.migrations import MIGRATIONS_DIR, apply_pending, discover

from tests.fakes import FakePgPool


def _write(directory: Path, name: str, sql: str = "SELECT 1;") -> None:
    (directory / name).write_text(sql, encoding="utf-8")


class TestDiscovery:
    def test_finds_the_shipped_migrations(self) -> None:
        versions = [m.version for m in discover()]

        assert versions == sorted(versions)
        assert 1 in versions, "0001_conversations.sql should be discoverable"

    def test_orders_numerically_not_lexically(self, tmp_path: Path) -> None:
        _write(tmp_path, "0002_b.sql")
        _write(tmp_path, "0010_c.sql")
        _write(tmp_path, "0001_a.sql")

        assert [m.version for m in discover(tmp_path)] == [1, 2, 10]

    def test_a_misnamed_file_fails_loudly(self, tmp_path: Path) -> None:
        """A silently skipped migration is schema drift you find in production."""
        _write(tmp_path, "add_users.sql")

        with pytest.raises(ValueError, match="misnamed"):
            discover(tmp_path)

    def test_a_duplicate_version_fails_loudly(self, tmp_path: Path) -> None:
        _write(tmp_path, "0001_a.sql")
        _write(tmp_path, "0001_b.sql")

        with pytest.raises(ValueError, match="duplicate migration version"):
            discover(tmp_path)

    def test_a_missing_directory_is_not_an_error(self, tmp_path: Path) -> None:
        assert discover(tmp_path / "nope") == []

    def test_exposes_the_file_contents(self, tmp_path: Path) -> None:
        _write(tmp_path, "0001_a.sql", "CREATE TABLE x ();")

        assert discover(tmp_path)[0].sql == "CREATE TABLE x ();"


class TestShippedSchema:
    def test_the_conversations_migration_creates_both_tables(self) -> None:
        sql = (MIGRATIONS_DIR / "0001_conversations.sql").read_text(encoding="utf-8")

        assert "CREATE TABLE IF NOT EXISTS conversations" in sql
        assert "CREATE TABLE IF NOT EXISTS conversation_messages" in sql

    def test_messages_cascade_when_a_conversation_is_deleted(self) -> None:
        sql = (MIGRATIONS_DIR / "0001_conversations.sql").read_text(encoding="utf-8")

        assert "ON DELETE CASCADE" in sql

    def test_turn_order_is_unique_per_conversation(self) -> None:
        sql = (MIGRATIONS_DIR / "0001_conversations.sql").read_text(encoding="utf-8")

        assert "UNIQUE (conversation_id, position)" in sql


class TestApply:
    async def test_applies_pending_migrations_in_order(self, tmp_path: Path) -> None:
        _write(tmp_path, "0001_a.sql")
        _write(tmp_path, "0002_b.sql")
        pool = FakePgPool()
        pool.fetch_result = []

        applied = await apply_pending(pool, tmp_path)

        assert applied == [1, 2]

    async def test_skips_versions_already_recorded(self, tmp_path: Path) -> None:
        _write(tmp_path, "0001_a.sql")
        _write(tmp_path, "0002_b.sql")
        pool = FakePgPool()
        pool.fetch_result = [{"version": 1}]

        applied = await apply_pending(pool, tmp_path)

        assert applied == [2]

    async def test_records_each_applied_version(self, tmp_path: Path) -> None:
        _write(tmp_path, "0001_a.sql")
        pool = FakePgPool()

        await apply_pending(pool, tmp_path)

        inserts = [sql for sql, _ in pool.executed if "INSERT INTO schema_migrations" in sql]
        assert len(inserts) == 1

    async def test_each_migration_commits_with_its_bookkeeping_row(self, tmp_path: Path) -> None:
        """Otherwise a crash can leave a version recorded but unapplied."""
        _write(tmp_path, "0001_a.sql")
        _write(tmp_path, "0002_b.sql")
        pool = FakePgPool()

        await apply_pending(pool, tmp_path)

        assert pool.transactions == 2

    async def test_takes_an_advisory_lock_so_replicas_cannot_race(self, tmp_path: Path) -> None:
        _write(tmp_path, "0001_a.sql")
        pool = FakePgPool()

        await apply_pending(pool, tmp_path)

        statements = [sql for sql, _ in pool.executed]
        assert any("pg_advisory_lock" in s for s in statements)
        assert any("pg_advisory_unlock" in s for s in statements)

    async def test_releases_the_lock_even_when_a_migration_fails(self, tmp_path: Path) -> None:
        """A stuck advisory lock would wedge every future boot."""
        _write(tmp_path, "0001_a.sql")
        # Reading the SQL raises: on Windows and POSIX alike, a directory where a
        # file is expected fails the read rather than the lock.
        (tmp_path / "0001_a.sql").unlink()
        (tmp_path / "0001_a.sql").mkdir()
        pool = FakePgPool()

        with pytest.raises(OSError):
            await apply_pending(pool, tmp_path)

        assert any("pg_advisory_unlock" in sql for sql, _ in pool.executed)

    async def test_no_migrations_is_a_no_op(self, tmp_path: Path) -> None:
        pool = FakePgPool()

        assert await apply_pending(pool, tmp_path) == []
        assert pool.executed == []
