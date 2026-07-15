"""Tests for conversation persistence and the Redis read-through cache.

The cache tests are the load-bearing ones: the whole design rests on Redis never
being the source of truth (ADR 0008), and these pin the properties that make
that claim true — a dead Redis costs latency and nothing else, and a write
always invalidates.

Note what the Postgres tests do and do not prove. ``FakePgPool`` records
statements rather than executing them, so these assert that the right SQL is
issued with the right arguments — not that Postgres parses it. That needs a live
database; see ``tests/unit/test_integration_agent.py``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from services.orchestrator.conversations import (
    CachedConversationStore,
    ConversationStore,
    NullConversationStore,
    PostgresConversationStore,
    Turn,
    build_conversation_store,
)

from tests.fakes import FakePgPool, FakeRedis, InMemoryConversationStore

if TYPE_CHECKING:
    from collections.abc import Sequence

_KEY = "conversation:abc"


def _messages() -> list[Turn]:
    return [
        Turn(role="user", content="hello"),
        Turn(role="assistant", content="hi"),
    ]


class TestNullConversationStore:
    async def test_loads_nothing(self) -> None:
        assert await NullConversationStore().load("abc") == []

    async def test_append_is_a_no_op(self) -> None:
        store = NullConversationStore()
        await store.append("abc", _messages())
        assert await store.load("abc") == []

    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(NullConversationStore(), ConversationStore)


class TestPostgresConversationStore:
    async def test_load_reads_messages_in_position_order(self) -> None:
        pool = FakePgPool()
        pool.fetch_result = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        history = await PostgresConversationStore(pool).load("abc")

        assert history == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        sql, args = pool.fetched[0]
        assert "ORDER BY position" in sql
        assert args == ("abc",)

    async def test_append_upserts_the_conversation_then_inserts_messages(self) -> None:
        pool = FakePgPool()
        pool.fetchval_result = 0

        await PostgresConversationStore(pool).append("abc", _messages())

        assert "INSERT INTO conversations" in pool.executed[0][0]
        assert "ON CONFLICT (id) DO UPDATE" in pool.executed[0][0]
        sql, rows = pool.executed_many[0]
        assert "INSERT INTO conversation_messages" in sql
        assert rows == [("abc", 0, "user", "hello"), ("abc", 1, "assistant", "hi")]

    async def test_append_continues_numbering_from_the_high_water_mark(self) -> None:
        pool = FakePgPool()
        pool.fetchval_result = 4

        await PostgresConversationStore(pool).append("abc", _messages())

        _, rows = pool.executed_many[0]
        assert [row[1] for row in rows] == [4, 5]

    async def test_append_runs_in_one_transaction(self) -> None:
        """A half-written turn is worse than none: the FK and the rows commit together."""
        pool = FakePgPool()
        pool.fetchval_result = 0

        await PostgresConversationStore(pool).append("abc", _messages())

        assert pool.transactions == 1

    async def test_appending_nothing_touches_the_database(self) -> None:
        pool = FakePgPool()

        await PostgresConversationStore(pool).append("abc", [])

        assert pool.executed == []
        assert pool.executed_many == []


class TestCacheReads:
    async def test_a_miss_falls_through_and_populates(self) -> None:
        inner = InMemoryConversationStore()
        inner.conversations["abc"] = [{"role": "user", "content": "hello"}]
        redis = FakeRedis()

        history = await CachedConversationStore(inner, redis, ttl_seconds=60).load("abc")

        assert history == [{"role": "user", "content": "hello"}]
        assert inner.load_calls == 1
        assert redis.decoded(_KEY) == history

    async def test_a_hit_does_not_touch_the_inner_store(self) -> None:
        inner = InMemoryConversationStore()
        redis = FakeRedis()
        redis.store[_KEY] = json.dumps([{"role": "user", "content": "cached"}])

        history = await CachedConversationStore(inner, redis, ttl_seconds=60).load("abc")

        assert history == [{"role": "user", "content": "cached"}]
        assert inner.load_calls == 0

    async def test_populating_applies_the_configured_ttl(self) -> None:
        redis = FakeRedis()

        await CachedConversationStore(InMemoryConversationStore(), redis, ttl_seconds=90).load(
            "abc"
        )

        assert redis.ttls[_KEY] == 90


class TestCacheDegradation:
    """Redis is an optimisation. Losing it must cost latency, never correctness."""

    async def test_a_dead_redis_still_serves_the_read_from_postgres(self) -> None:
        inner = InMemoryConversationStore()
        inner.conversations["abc"] = [{"role": "user", "content": "hello"}]

        history = await CachedConversationStore(inner, FakeRedis(fails=True), ttl_seconds=60).load(
            "abc"
        )

        assert history == [{"role": "user", "content": "hello"}]

    async def test_a_dead_redis_does_not_fail_an_append(self) -> None:
        inner = InMemoryConversationStore()

        await CachedConversationStore(inner, FakeRedis(fails=True), ttl_seconds=60).append(
            "abc", _messages()
        )

        assert inner.append_calls == 1

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param("{not json", id="unparseable"),
            pytest.param('{"role": "user"}', id="not-a-list"),
        ],
    )
    async def test_a_corrupt_entry_is_treated_as_a_miss(self, payload: str) -> None:
        inner = InMemoryConversationStore()
        inner.conversations["abc"] = [{"role": "user", "content": "real"}]
        redis = FakeRedis()
        redis.store[_KEY] = payload

        history = await CachedConversationStore(inner, redis, ttl_seconds=60).load("abc")

        assert history == [{"role": "user", "content": "real"}]


class TestCacheWrites:
    async def test_append_writes_through_to_the_inner_store(self) -> None:
        inner = InMemoryConversationStore()

        await CachedConversationStore(inner, FakeRedis(), ttl_seconds=60).append("abc", _messages())

        assert inner.conversations["abc"] == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    async def test_append_invalidates_rather_than_rewriting(self) -> None:
        """Deleting cannot disagree with Postgres; rewriting can."""
        redis = FakeRedis()
        redis.store[_KEY] = json.dumps([{"role": "user", "content": "stale"}])

        await CachedConversationStore(InMemoryConversationStore(), redis, ttl_seconds=60).append(
            "abc", _messages()
        )

        assert _KEY not in redis.store
        assert redis.deletes == [_KEY]
        assert redis.sets == []

    async def test_a_read_after_an_append_sees_the_new_turn(self) -> None:
        """The end-to-end property the invalidation exists for."""
        inner = InMemoryConversationStore()
        redis = FakeRedis()
        store = CachedConversationStore(inner, redis, ttl_seconds=60)
        await store.load("abc")  # caches the empty history

        await store.append("abc", _messages())

        assert await store.load("abc") == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    async def test_postgres_is_written_before_the_cache_is_invalidated(self) -> None:
        """Invalidate-first leaves a window where a read caches pre-append state."""
        order: list[str] = []

        class RecordingInner(InMemoryConversationStore):
            async def append(self, conversation_id: str, messages: Sequence[Turn]) -> None:
                order.append("postgres")
                await super().append(conversation_id, messages)

        class RecordingRedis(FakeRedis):
            async def delete(self, key: str) -> None:
                order.append("invalidate")
                await super().delete(key)

        await CachedConversationStore(RecordingInner(), RecordingRedis(), ttl_seconds=60).append(
            "abc", _messages()
        )

        assert order == ["postgres", "invalidate"]


class TestBuildConversationStore:
    def test_no_postgres_means_no_persistence(self) -> None:
        store = build_conversation_store(postgres_pool=None, redis=FakeRedis(), ttl_seconds=60)

        assert isinstance(store, NullConversationStore)

    def test_redis_alone_is_never_promoted_to_source_of_truth(self) -> None:
        """An eviction would silently become data loss."""
        store = build_conversation_store(postgres_pool=None, redis=FakeRedis(), ttl_seconds=60)

        assert not isinstance(store, CachedConversationStore)

    def test_postgres_without_redis_is_uncached(self) -> None:
        store = build_conversation_store(postgres_pool=FakePgPool(), redis=None, ttl_seconds=60)

        assert isinstance(store, PostgresConversationStore)

    def test_both_gives_a_cached_store(self) -> None:
        store = build_conversation_store(
            postgres_pool=FakePgPool(), redis=FakeRedis(), ttl_seconds=60
        )

        assert isinstance(store, CachedConversationStore)
