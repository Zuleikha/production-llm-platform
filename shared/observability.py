"""Observability primitives — the ``@traced`` decorator.

``@traced`` does two things on every call it wraps, for both sync and async
callables, preserving the wrapped signature for static typing:

1. **A structured DEBUG log** on entry, exit (with duration) and exception. This
   is the Stage 1 behaviour, unchanged — it works with no OpenTelemetry
   configured at all, and it is what you read when the trace pipeline is the
   thing that is broken.
2. **A real OpenTelemetry span** (Stage 5, ADR 0016): started on entry, ended on
   exit, exception recorded and status set to ERROR on the way out. Additive —
   point 1 did not become a fallback for point 2.

**This module imports the OpenTelemetry API, never the SDK.** That is what keeps
the arrow pointing the right way: ``shared/`` must not import ``services/``, and
the SDK — providers, exporters, the profile-keyed choice between them — lives in
``services/monitoring/tracing.py``. Until something calls
``configure_tracing()``, the API's global provider is a no-op and the spans below
cost close to nothing. So a script, an import, or a unit test that never wires
tracing emits nothing and dials nothing, which is the same "not configured means
never dialled" rule the datastores follow (ADR 0005).

**Span hygiene (CLAUDE.md, ADR 0016).** The rule against logging PII, secrets,
tokens, queries and excerpts applies identically to span attributes — a span goes
to Tempo, which is one more place data can leak. The attributes below are
therefore derived from the *function object at decoration time*, never from
arguments or return values: there is no code path here that can read a wrapped
call's data, which is a stronger guarantee than a rule someone has to remember.
Anything richer belongs on a span the caller creates deliberately, having thought
about what it carries.

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

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

_tracer_log = logging.getLogger("trace")

# Resolved once at import. `get_tracer` returns a ProxyTracer that forwards to
# whatever provider is global *at call time*, so this is safe to bind before
# configure_tracing() runs — which it always is, since decorators execute at
# import and the lifespan does not.
_tracer = trace.get_tracer("shared.observability")


def traced[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Trace entry, exit and errors of ``func`` — as a DEBUG log and an OTel span.

    Works transparently for both coroutine and regular functions.
    """
    span_name = getattr(func, "__qualname__", getattr(func, "__name__", "unknown"))
    # Static code identity, read off the function object once, at decoration.
    # Never anything from the call — see the module docstring on hygiene.
    attributes = {
        "code.function": span_name,
        "code.namespace": getattr(func, "__module__", "unknown"),
    }

    if inspect.iscoroutinefunction(func):
        async_func = cast(Callable[P, Awaitable[R]], func)

        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start = time.perf_counter()
            _tracer_log.debug("trace.enter", extra={"span": span_name})
            with _tracer.start_as_current_span(
                span_name,
                attributes=attributes,
                # We record the exception and set the status ourselves in
                # `_record_error`, so the status description stays the type name
                # only. Left on, the SDK's own handler overwrites it with
                # `f"{type}: {message}"` — putting the runtime message on the one
                # span field a hygiene rule most wants clean. See ADR 0016.
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                try:
                    result = await async_func(*args, **kwargs)
                except Exception as exc:
                    _record_error(span, exc)
                    _tracer_log.debug("trace.error", extra={"span": span_name}, exc_info=True)
                    raise
                duration_ms = round((time.perf_counter() - start) * 1000, 3)
                _tracer_log.debug(
                    "trace.exit", extra={"span": span_name, "duration_ms": duration_ms}
                )
                return result

        return cast(Callable[P, R], async_wrapper)

    @functools.wraps(func)
    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        _tracer_log.debug("trace.enter", extra={"span": span_name})
        with _tracer.start_as_current_span(
            span_name,
            attributes=attributes,
            # See the async wrapper: we own the exception recording and status in
            # `_record_error`, so the status description stays the type name only.
            # Left on, the SDK's handler overwrites it with `f"{type}: {message}"`,
            # putting the runtime message on the span field hygiene most wants
            # clean. See ADR 0016.
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                _record_error(span, exc)
                _tracer_log.debug("trace.error", extra={"span": span_name}, exc_info=True)
                raise
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            _tracer_log.debug("trace.exit", extra={"span": span_name, "duration_ms": duration_ms})
            return result

    return sync_wrapper


def _record_error(span: trace.Span, exc: Exception) -> None:
    """Mark ``span`` failed and attach the exception.

    The status description is the exception's **type name only**. The type is a
    code identifier; the message is the one runtime-derived string that reaches a
    span at all, and it is confined to the recorded exception event rather than
    repeated as a searchable status. That event carries the message and traceback
    — exactly what the DEBUG log beside it has written with ``exc_info=True``
    since Stage 1 — so tracing widens where that text goes, not what it is. This
    codebase's exceptions carry code-level detail rather than user or document
    text (``ToolError`` reports a type name, not the query), and any new exception
    whose message embeds untrusted input is a hygiene problem in the raiser, which
    is where it should be fixed. See ADR 0016.
    """
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
