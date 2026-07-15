"""Test doubles shared across test modules.

Kept out of ``conftest.py`` deliberately: pytest imports conftest as a top-level
module, so importing it again by path would create a second, distinct module
object. A plain module has no such hazard.

The datastore driver doubles below (:class:`FakeRedis`, :class:`FakePgPool`)
implement only the narrow surface the code under test actually uses. They are
honest about what they are: ``FakePgPool`` **does not parse or execute SQL**, so
a test using it proves that the right statement was issued with the right
arguments, not that Postgres accepts it. Verifying the SQL itself needs a real
Postgres — see the integration tests.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from shared.datastores import Datastore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from services.orchestrator.conversations import Turn


class FakeDatastore(Datastore):
    """A Datastore whose ping/connect behaviour is dictated by the test."""

    def __init__(self, name: str, *, configured: bool = True, fails: bool = False) -> None:
        self._name = name
        self._configured = configured
        self._fails = fails
        self.connect_calls = 0
        self.close_calls = 0
        self.ping_calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def configured(self) -> bool:
        return self._configured

    async def connect(self) -> None:
        self.connect_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def ping(self) -> None:
        self.ping_calls += 1
        if self._fails:
            raise ConnectionError(f"{self._name} is down")


class InMemoryConversationStore:
    """A ConversationStore backed by a dict. Stands in for Postgres.

    Used where the test is about the layer *above* persistence (the cache, the
    orchestrator) and a real database would only add setup.
    """

    def __init__(self) -> None:
        self.conversations: dict[str, list[dict[str, str]]] = {}
        self.load_calls = 0
        self.append_calls = 0

    async def load(self, conversation_id: str) -> list[dict[str, str]]:
        self.load_calls += 1
        return list(self.conversations.get(conversation_id, []))

    async def append(self, conversation_id: str, messages: Sequence[Turn]) -> None:
        self.append_calls += 1
        stored = self.conversations.setdefault(conversation_id, [])
        stored.extend({"role": m.role, "content": m.content} for m in messages)


class FakeRedis:
    """The three Redis commands the conversation cache uses.

    Faithful enough for the cache's semantics (get/set/delete + TTL recording),
    and able to fail on demand so the degradation paths are testable.
    """

    def __init__(self, *, fails: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fails = fails
        self.gets: list[str] = []
        self.sets: list[str] = []
        self.deletes: list[str] = []

    def _guard(self) -> None:
        if self.fails:
            raise ConnectionError("redis is down")

    async def get(self, key: str) -> str | None:
        self.gets.append(key)
        self._guard()
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.sets.append(key)
        self._guard()
        self.store[key] = value
        self.ttls[key] = ex

    async def delete(self, key: str) -> None:
        self.deletes.append(key)
        self._guard()
        self.store.pop(key, None)

    def decoded(self, key: str) -> Any:
        return json.loads(self.store[key])


class FakePgConnection:
    """Records statements instead of executing them. See the module docstring."""

    def __init__(self, pool: FakePgPool) -> None:
        self._pool = pool

    async def execute(self, sql: str, *args: Any) -> None:
        self._pool.executed.append((sql, args))

    async def executemany(self, sql: str, args: Sequence[Sequence[Any]]) -> None:
        self._pool.executed_many.append((sql, [tuple(a) for a in args]))

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self._pool.fetched.append((sql, args))
        return self._pool.fetch_result

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self._pool.fetched.append((sql, args))
        return self._pool.fetchval_result

    def transaction(self) -> FakePgTransaction:
        self._pool.transactions += 1
        return FakePgTransaction()


class FakePgTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakePgAcquire:
    def __init__(self, pool: FakePgPool) -> None:
        self._pool = pool

    async def __aenter__(self) -> FakePgConnection:
        return FakePgConnection(self._pool)

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakePgPool:
    """The asyncpg pool surface the conversation store and migrations use."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.executed_many: list[tuple[str, list[tuple[Any, ...]]]] = []
        self.fetched: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_result: list[dict[str, Any]] = []
        self.fetchval_result: Any = None
        self.transactions = 0

    def acquire(self) -> FakePgAcquire:
        return FakePgAcquire(self)

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetched.append((sql, args))
        return self.fetch_result

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.fetched.append((sql, args))
        return self.fetchval_result

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql, args))
