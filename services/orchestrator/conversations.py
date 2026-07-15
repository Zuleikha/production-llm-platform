"""Conversation history: Postgres is the truth, Redis is a read-through cache.

The split is the whole design (ADR 0008):

- **Postgres owns the data.** Every write goes there first and synchronously. A
  conversation that Postgres never accepted did not happen.
- **Redis only ever holds a copy.** Losing Redis entirely costs latency, never
  history — every read falls through to Postgres and repopulates.
- **Writes invalidate, they do not update.** The cache entry for a conversation
  is deleted on append rather than rewritten. Rewriting means computing the new
  value in two places (here and in Postgres) and hoping they agree; deleting
  cannot disagree with anything. The next read pays one Postgres query.

A store with no Postgres configured degrades to :class:`NullConversationStore`,
which persists nothing — that is what lets the ``test`` profile and a bare
``uvicorn`` run with no datastores at all, exactly as Stage 2's `not_configured`
does for ``/ready``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from shared.logging import get_logger
from shared.observability import traced

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

    import asyncpg

_logger = get_logger("orchestrator.conversations")

_CACHE_PREFIX = "conversation:"


@dataclass(frozen=True, slots=True)
class Turn:
    """One stored message in a conversation.

    Deliberately **not** ``services.api.schemas.ChatMessage``. The dependency
    runs api -> orchestrator; importing the API's wire model back down here
    inverts that, and in fact created an import cycle (`services.api.__init__`
    builds the app, which imports this module). The API converts at its own
    boundary, which is where wire-format concerns belong.
    """

    role: str
    content: str


@runtime_checkable
class ConversationStore(Protocol):
    """Reads and appends the message history of one conversation."""

    async def load(self, conversation_id: str) -> list[dict[str, str]]:
        """Return every stored message, oldest first. Empty if unknown."""
        ...

    async def append(self, conversation_id: str, messages: Sequence[Turn]) -> None:
        """Append ``messages`` to the conversation, creating it if needed."""
        ...


class CacheBackend(Protocol):
    """The three Redis commands the conversation cache actually uses.

    Depending on this rather than on ``redis.asyncio.Redis`` keeps the cache
    honest about its requirements: it needs a keyed store with a TTL, not a
    Redis. A narrow port is also what makes the cache's failure paths testable
    without running a Redis to break.

    The signatures are shaped so the real ``redis.asyncio.Redis`` satisfies this
    structurally: the key is **positional-only** (redis-py calls it ``name``),
    and the methods are declared as returning an awaitable rather than as
    ``async def`` (redis-py's are typed ``Awaitable``, not coroutines).
    """

    def get(self, key: str, /) -> Awaitable[Any]: ...

    def set(self, key: str, value: str, /, *, ex: int | None = None) -> Awaitable[Any]: ...

    def delete(self, key: str, /) -> Awaitable[Any]: ...


class NullConversationStore:
    """Persists nothing. Used when no Postgres is configured.

    Not a stub: it is the honest implementation of "there is nowhere to write".
    A conversation simply has no history, which is the correct behaviour for a
    stateless run rather than an error.
    """

    async def load(self, conversation_id: str) -> list[dict[str, str]]:
        return []

    async def append(self, conversation_id: str, messages: Sequence[Turn]) -> None:
        return None


class PostgresConversationStore:
    """The source of truth. Raw asyncpg, matching the rest of the codebase."""

    def __init__(self, pool: asyncpg.Pool[asyncpg.Record]) -> None:
        self._pool = pool

    @traced
    async def load(self, conversation_id: str) -> list[dict[str, str]]:
        rows = await self._pool.fetch(
            """
            SELECT role, content
              FROM conversation_messages
             WHERE conversation_id = $1
             ORDER BY position
            """,
            conversation_id,
        )
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    @traced
    async def append(self, conversation_id: str, messages: Sequence[Turn]) -> None:
        if not messages:
            return
        async with self._pool.acquire() as connection, connection.transaction():
            # Upsert the parent first so the FK holds, and bump updated_at so a
            # returning conversation is distinguishable from a dormant one.
            await connection.execute(
                """
                INSERT INTO conversations (id) VALUES ($1)
                ON CONFLICT (id) DO UPDATE SET updated_at = now()
                """,
                conversation_id,
            )
            # Read the high-water mark inside the same transaction as the insert:
            # two concurrent appends would otherwise compute the same starting
            # position and collide on the (conversation_id, position) unique
            # index. The collision is a loud 23505 rather than silent
            # interleaving, but taking the row lock avoids it entirely.
            next_position: int = (
                await connection.fetchval(
                    """
                    SELECT COALESCE(MAX(position) + 1, 0)
                      FROM conversation_messages
                     WHERE conversation_id = $1
                    """,
                    conversation_id,
                )
                or 0
            )
            await connection.executemany(
                """
                INSERT INTO conversation_messages (conversation_id, position, role, content)
                VALUES ($1, $2, $3, $4)
                """,
                [
                    (conversation_id, next_position + offset, message.role, message.content)
                    for offset, message in enumerate(messages)
                ],
            )
        _logger.info(
            "conversation.appended",
            extra={"conversation_id": conversation_id, "messages": len(messages)},
        )


class CachedConversationStore:
    """Read-through Redis cache in front of another store.

    Wraps rather than replaces: the inner store stays the source of truth, and
    this class only ever avoids a read. See ADR 0008.
    """

    def __init__(
        self,
        inner: ConversationStore,
        redis: CacheBackend,
        *,
        ttl_seconds: int,
    ) -> None:
        self._inner = inner
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _key(conversation_id: str) -> str:
        return f"{_CACHE_PREFIX}{conversation_id}"

    @traced
    async def load(self, conversation_id: str) -> list[dict[str, str]]:
        """Return history from Redis if present, else from the inner store.

        A Redis failure is logged and swallowed *here specifically* — this is
        the one place where degrading beats failing loud, because the cache is
        an optimisation and Postgres can still answer. The request survives a
        dead Redis; it would not survive a dead Postgres, and `append` below
        does not swallow.
        """
        try:
            cached = await self._redis.get(self._key(conversation_id))
        except Exception as exc:
            _logger.warning(
                "conversation.cache_read_failed",
                extra={"conversation_id": conversation_id},
                exc_info=exc,
            )
        else:
            if cached is not None:
                messages = self._decode(cached, conversation_id)
                if messages is not None:
                    _logger.info(
                        "conversation.cache_hit", extra={"conversation_id": conversation_id}
                    )
                    return messages

        messages = await self._inner.load(conversation_id)
        await self._populate(conversation_id, messages)
        return messages

    def _decode(self, cached: Any, conversation_id: str) -> list[dict[str, str]] | None:
        """Parse a cache entry, treating corruption as a miss rather than a 500.

        A malformed entry means someone wrote the key by hand or the format
        changed under a running deploy. Neither is worth failing a request over
        when the source of truth is one query away.
        """
        try:
            decoded = json.loads(cached)
        except (TypeError, ValueError) as exc:
            _logger.warning(
                "conversation.cache_corrupt",
                extra={"conversation_id": conversation_id},
                exc_info=exc,
            )
            return None
        if not isinstance(decoded, list):
            _logger.warning(
                "conversation.cache_corrupt", extra={"conversation_id": conversation_id}
            )
            return None
        return [m for m in decoded if isinstance(m, dict)]

    async def _populate(self, conversation_id: str, messages: list[dict[str, str]]) -> None:
        try:
            await self._redis.set(self._key(conversation_id), json.dumps(messages), ex=self._ttl)
        except Exception as exc:
            _logger.warning(
                "conversation.cache_write_failed",
                extra={"conversation_id": conversation_id},
                exc_info=exc,
            )

    @traced
    async def append(self, conversation_id: str, messages: Sequence[Turn]) -> None:
        """Write through to the inner store, then drop the cache entry.

        Order matters: Postgres first, invalidate second. Invalidating first
        leaves a window where a concurrent read repopulates the cache from the
        pre-append state and then the entry survives the TTL as stale history.
        A failure to invalidate is logged, not swallowed silently — but it also
        must not fail a request whose data is already durably committed.
        """
        await self._inner.append(conversation_id, messages)
        try:
            await self._redis.delete(self._key(conversation_id))
        except Exception as exc:
            _logger.warning(
                "conversation.cache_invalidate_failed",
                extra={"conversation_id": conversation_id},
                exc_info=exc,
            )


@traced
def build_conversation_store(
    *,
    postgres_pool: asyncpg.Pool[asyncpg.Record] | None,
    redis: CacheBackend | None,
    ttl_seconds: int,
) -> ConversationStore:
    """Assemble the store the configured datastores can actually support.

    No Postgres means no persistence at all — Redis alone is never promoted to
    the source of truth, because an eviction would silently become data loss.
    """
    if postgres_pool is None:
        _logger.info("conversation.store_selected", extra={"store": "null"})
        return NullConversationStore()
    store: ConversationStore = PostgresConversationStore(postgres_pool)
    if redis is None:
        _logger.info("conversation.store_selected", extra={"store": "postgres"})
        return store
    _logger.info("conversation.store_selected", extra={"store": "postgres+redis"})
    return CachedConversationStore(store, redis, ttl_seconds=ttl_seconds)
