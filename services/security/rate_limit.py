"""Redis-backed, per-principal rate limiting — atomic, and fail-open.

Keyed by the authenticated principal id (ADR 0019). Redis-backed rather than
in-memory because Stage 7 ships configurable replica counts and an optional HPA:
an in-memory counter would silently under-count the moment there is more than one
pod, which this project's own Kubernetes work makes a real bug, not a
hypothetical one.

**The increment-and-check is a single atomic server-side operation** — a Lua
script that ``INCR``s the counter and sets the window TTL on the first hit only.
A read-then-write would race across replicas (two pods each read N, each write
N+1, and 2 requests count as 1); the whole point of doing it in Redis is that the
counter is authoritative and the mutation is atomic.

**Fail-open on Redis unavailability.** An unreachable or erroring Redis logs a
degraded event and *allows* the request, matching the precedent already accepted
for the conversation cache (ADR 0008: a Redis failure degrades service rather
than fails loud). A rate limiter that failed closed would turn a cache outage
into a full outage of the paid LLM endpoint — a worse failure mode than the rate
limit briefly not applying. The degradation is logged **every time** it happens,
never silently absorbed.

The Redis client is reused from :attr:`DatastoreRegistry.redis_client` (the
existing seam) and read at call time, so this opens no second connection path and
a store that appears/disappears across the process lifetime is handled uniformly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from shared.logging import get_logger
from shared.metrics import RATE_LIMITER_FAIL_OPEN
from shared.observability import traced

if TYPE_CHECKING:
    from shared.config import Settings

_logger = get_logger("security.rate_limit")

# INCR the counter, then set the window TTL only when the counter is new (== 1),
# so the window is fixed from its first request rather than slid forward by every
# one. Returns the post-increment count. Atomic: Redis runs the whole script
# without interleaving another client's commands.
_INCR_AND_EXPIRE = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class EvalClient(Protocol):
    """The single Redis command the limiter needs: server-side script ``eval``."""

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        """Run ``script`` against ``numkeys`` keys; returns an awaitable result."""
        ...


class RedisSource(Protocol):
    """The narrow view of the datastore registry the limiter reads.

    Structural, so :class:`~shared.datastores.DatastoreRegistry` satisfies it
    without an import-time coupling, and a test can supply a fake.
    """

    @property
    def redis_client(self) -> EvalClient | None:
        """The live Redis client, or ``None`` when unconfigured/not connected."""
        ...


class RedisRateLimiter:
    """A fixed-window per-principal limiter over the shared Redis seam."""

    def __init__(
        self,
        source: RedisSource,
        *,
        limit: int,
        window_seconds: int,
        key_prefix: str = "ratelimit",
    ) -> None:
        self._source = source
        self._limit = limit
        self._window = window_seconds
        self._key_prefix = key_prefix

    @traced
    async def check(self, principal_id: str) -> bool:
        """Count this request against ``principal_id``; return whether it's allowed.

        ``True`` when the principal is at or under the limit for the current
        window, ``False`` when over it. Fail-open: an unavailable or failing
        Redis logs ``ratelimit.degraded`` and returns ``True``.
        """
        client = self._source.redis_client
        if client is None:
            # Fail-open (ADR 0008/0019). Counter drives the Stage 9 fail-open
            # alert (ADR 0020) — the event is now visible, not just logged.
            RATE_LIMITER_FAIL_OPEN.inc()
            _logger.warning("ratelimit.degraded", extra={"reason": "redis_unavailable"})
            return True
        key = f"{self._key_prefix}:{principal_id}"
        try:
            count = int(await client.eval(_INCR_AND_EXPIRE, 1, key, self._window))
        except Exception as exc:
            # Fail-open (ADR 0008/0019): a limiter outage must not become an
            # endpoint outage. The event is logged and counted every time, never
            # swallowed. The counter backs the Stage 9 fail-open alert (ADR 0020).
            RATE_LIMITER_FAIL_OPEN.inc()
            _logger.warning("ratelimit.degraded", extra={"reason": "redis_error"}, exc_info=exc)
            return True
        allowed = count <= self._limit
        if not allowed:
            _logger.info(
                "ratelimit.exceeded",
                extra={"principal": principal_id, "count": count, "limit": self._limit},
            )
        return allowed


@traced
def build_rate_limiter(settings: Settings, source: RedisSource) -> RedisRateLimiter:
    """Assemble the limiter from configuration and the datastore registry."""
    return RedisRateLimiter(
        source,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
