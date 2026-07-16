"""Datastore connection layer: pooled clients with a uniform probe contract.

Each backing store (PostgreSQL, Redis, Qdrant) is wrapped in a :class:`Datastore`
that owns a **connection pool**, not a single connection, and exposes the same
four-part contract: ``configured``, ``connect``, ``close``, ``ping``. The
:class:`DatastoreRegistry` fans those out for the application lifespan
(``startup``/``shutdown``) and for readiness (``probe``). See ADR 0005.

Two rules shape the design:

- **Startup never crashes the process.** A datastore that cannot be reached at
  boot is recorded as failed and reported by ``/ready``, so the container starts
  and its orchestrator sees an honest un-ready pod rather than a crash loop.
- **Unconfigured is not the same as healthy.** A store with no URL reports
  ``not_configured`` and is never dialled — that keeps the test profile hermetic.
  It is *not* a silent pass in production: ``Settings`` requires all three URLs
  under the ``prod`` profile, so a missing URL fails loudly at boot instead of
  surfacing here as a shrug. See ADR 0005.
"""

from __future__ import annotations

import asyncio
import math
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

import asyncpg

# Still a direct dependency after Stage 4 swapped Qdrant onto `qdrant-client`:
# the pool limits below are httpx's, and `httpx` remains the transport under
# both qdrant-client and Starlette's TestClient.
import httpx
import redis.asyncio as aioredis
from qdrant_client import AsyncQdrantClient

from shared.logging import get_logger
from shared.observability import traced

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shared.config import Settings

_logger = get_logger("shared.datastores")

ProbeStatus = Literal["ok", "unavailable", "not_configured"]


class Datastore(ABC):
    """A pooled connection to one backing store, with a uniform probe contract."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier used as the key in the ``/ready`` checks map."""

    @property
    @abstractmethod
    def configured(self) -> bool:
        """True when a URL was supplied; False stores are never dialled."""

    @abstractmethod
    async def connect(self) -> None:
        """Open the connection pool. Raises if the store cannot be reached."""

    @abstractmethod
    async def close(self) -> None:
        """Close the pool. Safe to call when never connected."""

    @abstractmethod
    async def ping(self) -> None:
        """Round-trip the store. Raises on failure, including when unconfigured."""


class PostgresDatastore(Datastore):
    """PostgreSQL, pooled via ``asyncpg.create_pool``."""

    def __init__(self, url: str | None, *, min_size: int, max_size: int, timeout: float) -> None:
        self._url = url
        self._min_size = min_size
        self._max_size = max_size
        self._timeout = timeout
        self._pool: asyncpg.Pool[asyncpg.Record] | None = None

    @property
    def name(self) -> str:
        return "postgres"

    @property
    def configured(self) -> bool:
        return self._url is not None

    @property
    def pool(self) -> asyncpg.Pool[asyncpg.Record] | None:
        """The live pool, or None when unconfigured or not yet connected.

        Exposed for Stage 3's conversation store, which needs to issue real
        queries. Returning None rather than raising keeps the caller's "no
        datastore, no persistence" path the same shape as `/ready`'s
        `not_configured`.
        """
        return self._pool

    @traced
    async def connect(self) -> None:
        if self._url is None:  # pragma: no cover - registry never connects these
            return
        self._pool = await asyncpg.create_pool(
            self._url,
            min_size=self._min_size,
            max_size=self._max_size,
            timeout=self._timeout,
        )

    @traced
    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @traced
    async def ping(self) -> None:
        if self._pool is None:
            raise RuntimeError("postgres is not connected")
        await self._pool.fetchval("SELECT 1")


class RedisDatastore(Datastore):
    """Redis, pooled via ``redis.asyncio``'s built-in connection pool."""

    def __init__(self, url: str | None, *, max_connections: int, timeout: float) -> None:
        self._url = url
        self._max_connections = max_connections
        self._timeout = timeout
        self._client: aioredis.Redis | None = None

    @property
    def name(self) -> str:
        return "redis"

    @property
    def configured(self) -> bool:
        return self._url is not None

    @property
    def client(self) -> aioredis.Redis | None:
        """The live client, or None when unconfigured or not yet connected.

        Exposed for Stage 3's conversation cache. See `PostgresDatastore.pool`
        for why this returns None rather than raising.
        """
        return self._client

    @traced
    async def connect(self) -> None:
        if self._url is None:  # pragma: no cover - registry never connects these
            return
        client: aioredis.Redis = aioredis.Redis.from_url(
            self._url,
            max_connections=self._max_connections,
            socket_connect_timeout=self._timeout,
            socket_timeout=self._timeout,
        )
        # from_url is lazy — force a round-trip so an unreachable Redis fails
        # here, at connect, rather than silently succeeding.
        await client.ping()
        self._client = client

    @traced
    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @traced
    async def ping(self) -> None:
        if self._client is None:
            raise RuntimeError("redis is not connected")
        await self._client.ping()


class QdrantDatastore(Datastore):
    """Qdrant, via ``qdrant-client``'s ``AsyncQdrantClient``.

    Stage 2 reached Qdrant with a raw pooled ``httpx.AsyncClient`` hitting
    ``/readyz``, because a readiness probe was all it needed and ``qdrant-client``
    was a Stage 4 dependency (ADR 0005). Stage 4 makes the swap ADR 0005
    anticipated: the collection now holds vectors, and hand-rolling
    search/upsert over raw HTTP would be reimplementing the client badly.

    The probe changes with it — ``get_collections`` instead of ``/readyz``. That
    is a slightly *stronger* check: it proves the API answers queries, not just
    that the process is up. See ADR 0012.
    """

    def __init__(self, url: str | None, *, max_connections: int, timeout: float) -> None:
        self._url = url
        self._max_connections = max_connections
        self._timeout = timeout
        self._client: AsyncQdrantClient | None = None

    @property
    def name(self) -> str:
        return "qdrant"

    @property
    def configured(self) -> bool:
        return self._url is not None

    @property
    def client(self) -> AsyncQdrantClient | None:
        """The live client, or None when unconfigured or not yet connected.

        Exposed for Stage 4's vector store. See `PostgresDatastore.pool` for why
        this returns None rather than raising.
        """
        return self._client

    @traced
    async def connect(self) -> None:
        if self._url is None:  # pragma: no cover - registry never connects these
            return
        client = AsyncQdrantClient(
            url=self._url,
            # AsyncQdrantClient's own timeout is typed `int | None`, unlike the
            # float the other stores take; a sub-second timeout would truncate
            # to 0 and mean "no timeout", so round up rather than down.
            timeout=max(1, math.ceil(self._timeout)),
            # Forwarded to the httpx client underneath. Not in the constructor's
            # signature — it arrives via **kwargs — so it is pinned by a test
            # rather than by the type checker (see test_datastores.py).
            limits=httpx.Limits(max_connections=self._max_connections),
        )
        try:
            await self._probe(client)
        except Exception:
            await client.close()
            raise
        self._client = client

    @traced
    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    @traced
    async def ping(self) -> None:
        if self._client is None:
            raise RuntimeError("qdrant is not connected")
        await self._probe(self._client)

    @staticmethod
    async def _probe(client: AsyncQdrantClient) -> None:
        await client.get_collections()


class DatastoreRegistry:
    """Owns every datastore and fans lifecycle/probe calls out across them."""

    def __init__(self, stores: list[Datastore], *, probe_timeout: float = 2.0) -> None:
        self.datastores: Sequence[Datastore] = stores
        self._probe_timeout = probe_timeout
        # Stores whose connect() raised. Their pool was never opened, so they
        # are unusable until the process restarts — probe() must not trust a
        # ping from them.
        self._connect_failed: set[str] = set()

    @classmethod
    @traced
    def from_settings(cls, settings: Settings) -> DatastoreRegistry:
        """Build the three datastores, in a stable order, from configuration."""
        return cls(
            [
                PostgresDatastore(
                    settings.database_url,
                    min_size=settings.db_pool_min_size,
                    max_size=settings.db_pool_max_size,
                    timeout=settings.datastore_connect_timeout_seconds,
                ),
                RedisDatastore(
                    settings.redis_url,
                    max_connections=settings.redis_pool_max_connections,
                    timeout=settings.datastore_connect_timeout_seconds,
                ),
                QdrantDatastore(
                    settings.qdrant_url,
                    max_connections=settings.qdrant_pool_max_connections,
                    timeout=settings.datastore_connect_timeout_seconds,
                ),
            ],
            probe_timeout=settings.datastore_probe_timeout_seconds,
        )

    def get(self, name: str) -> Datastore | None:
        """Return the datastore called ``name``, or None if there isn't one."""
        return next((s for s in self.datastores if s.name == name), None)

    @property
    def postgres_pool(self) -> asyncpg.Pool[asyncpg.Record] | None:
        """The Postgres pool, if Postgres is configured and connected."""
        store = self.get("postgres")
        return store.pool if isinstance(store, PostgresDatastore) else None

    @property
    def redis_client(self) -> aioredis.Redis | None:
        """The Redis client, if Redis is configured and connected."""
        store = self.get("redis")
        return store.client if isinstance(store, RedisDatastore) else None

    @property
    def qdrant_client(self) -> AsyncQdrantClient | None:
        """The Qdrant client, if Qdrant is configured and connected.

        Exposed for Stage 4's vector store, which needs to issue real searches.
        """
        store = self.get("qdrant")
        return store.client if isinstance(store, QdrantDatastore) else None

    @traced
    async def startup(self) -> None:
        """Connect every configured datastore, concurrently. Never raises.

        A failure here is logged and recorded, not propagated: the service must
        boot and report the failure via ``/ready`` rather than crash-looping.

        Concurrent because serialising means an unreachable store delays liveness
        by the *sum* of every connect timeout — long enough for an orchestrator
        to kill the pod before it serves ``/health``, which is precisely the
        crash-loop this design exists to avoid.
        """
        await asyncio.gather(*(self._connect_one(s) for s in self.datastores))

    async def _connect_one(self, store: Datastore) -> None:
        if not store.configured:
            _logger.info("datastore.skipped", extra={"datastore": store.name})
            return
        try:
            await store.connect()
        except Exception as exc:
            self._connect_failed.add(store.name)
            _logger.error(
                "datastore.connect_failed",
                extra={"datastore": store.name},
                exc_info=exc,
            )
        else:
            self._connect_failed.discard(store.name)
            _logger.info("datastore.connected", extra={"datastore": store.name})

    @traced
    async def shutdown(self) -> None:
        """Close every datastore. One failing close must not strand the others."""
        for store in self.datastores:
            try:
                await store.close()
            except Exception as exc:
                _logger.error(
                    "datastore.close_failed",
                    extra={"datastore": store.name},
                    exc_info=exc,
                )
            else:
                _logger.info("datastore.closed", extra={"datastore": store.name})

    @traced
    async def probe(self) -> dict[str, ProbeStatus]:
        """Probe every datastore concurrently; map name -> status."""
        statuses = await asyncio.gather(*(self._probe_one(s) for s in self.datastores))
        return dict(zip((s.name for s in self.datastores), statuses, strict=True))

    async def _probe_one(self, store: Datastore) -> ProbeStatus:
        if not store.configured:
            return "not_configured"
        if store.name in self._connect_failed:
            return "unavailable"
        try:
            await asyncio.wait_for(store.ping(), timeout=self._probe_timeout)
        except Exception as exc:
            _logger.warning(
                "datastore.probe_failed",
                extra={"datastore": store.name},
                exc_info=exc,
            )
            return "unavailable"
        return "ok"
