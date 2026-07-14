"""Observability primitives — the ``@traced`` decorator.

Stage 1 provides a lightweight, dependency-free tracing seam: ``@traced`` emits
a structured DEBUG log on function entry, exit (with duration) and on exception.
It supports both sync and async callables and preserves the wrapped signature
for static typing.

Full OpenTelemetry span export (collector, sampling, propagation) is
**planned, not yet implemented** — it is deferred to Stage 5 (observability),
which will extend this exact decorator so call sites never change.

NOTE: The logging/observability primitives themselves (this module and
``shared.logging``) are intentionally NOT decorated with ``@traced`` — doing so
would recurse through the logging machinery.
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import cast

_tracer = logging.getLogger("trace")


def traced[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Trace entry, exit and errors of ``func`` via structured DEBUG logs.

    Works transparently for both coroutine and regular functions.
    """
    span = getattr(func, "__qualname__", getattr(func, "__name__", "unknown"))

    if inspect.iscoroutinefunction(func):
        async_func = cast(Callable[P, Awaitable[R]], func)

        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start = time.perf_counter()
            _tracer.debug("trace.enter", extra={"span": span})
            try:
                result = await async_func(*args, **kwargs)
            except Exception:
                _tracer.debug("trace.error", extra={"span": span}, exc_info=True)
                raise
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            _tracer.debug("trace.exit", extra={"span": span, "duration_ms": duration_ms})
            return result

        return cast(Callable[P, R], async_wrapper)

    @functools.wraps(func)
    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        _tracer.debug("trace.enter", extra={"span": span})
        try:
            result = func(*args, **kwargs)
        except Exception:
            _tracer.debug("trace.error", extra={"span": span}, exc_info=True)
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 3)
        _tracer.debug("trace.exit", extra={"span": span, "duration_ms": duration_ms})
        return result

    return sync_wrapper
