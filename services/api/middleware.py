"""HTTP middleware: request-id propagation, access logging, and metrics."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from shared.logging import get_logger, set_request_id
from shared.observability import traced
from starlette.middleware.base import BaseHTTPMiddleware

from services.api.metrics import REQUEST_COUNT, REQUEST_LATENCY

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

_logger = get_logger("api.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, time the request, log access, and record metrics.

    The request id is taken from the inbound ``X-Request-ID`` header when
    present (so it can be propagated across services) and otherwise generated.
    It is bound to the logging context and echoed back on the response.
    """

    @traced
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        set_request_id(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)

            duration = time.perf_counter() - start
            # Use the matched route template (not the raw path) to bound cardinality.
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            method = request.method
            status = response.status_code

            REQUEST_COUNT.labels(method=method, path=path, status=str(status)).inc()
            REQUEST_LATENCY.labels(method=method, path=path).observe(duration)

            response.headers[REQUEST_ID_HEADER] = request_id
            # Must be logged BEFORE the request id is unbound below, otherwise
            # the access log records request_id=null and correlation is lost.
            _logger.info(
                "http.request",
                extra={
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": round(duration * 1000, 3),
                },
            )
            return response
        finally:
            set_request_id(None)
