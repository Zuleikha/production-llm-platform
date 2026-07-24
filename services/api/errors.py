"""Global error handling: a uniform JSON error envelope for every failure.

All handlers emit the same shape so clients get a predictable contract:

    {"error": {"type": "...", "message": "...", "request_id": "..."}}

Unhandled exceptions are logged with a full trace and returned as a generic 500
that never leaks internals (see ADR 0003 / coding-standards "fail loud").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from shared.logging import get_logger, get_request_id
from shared.resilience import CircuitBreakerOpenError

# Register against Starlette's HTTPException, not FastAPI's subclass: the router
# raises the Starlette base for unmatched routes (404), and registering the
# subclass would miss it. FastAPI's HTTPException inherits from this, so both
# are covered by one handler.
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

_logger = get_logger("api.error")


class ErrorBody(BaseModel):
    """The inner error object."""

    type: str
    message: str
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Uniform error envelope returned by every error handler."""

    error: ErrorBody


def _render(status_code: int, error_type: str, message: str) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorBody(type=error_type, message=message, request_id=get_request_id())
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


async def http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render FastAPI/Starlette ``HTTPException`` in the uniform envelope.

    Starlette types every handler as taking a bare ``Exception``, so the
    concrete type is narrowed here rather than suppressed with an ignore.
    """
    if not isinstance(exc, HTTPException):  # pragma: no cover - registration guarantees type
        return _render(500, "internal_error", "An internal server error occurred.")
    detail = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    return _render(exc.status_code, "http_error", detail)


async def validation_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render request-validation failures as 422 in the uniform envelope."""
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - see above
        return _render(500, "internal_error", "An internal server error occurred.")
    return _render(422, "validation_error", str(exc.errors()))


async def circuit_breaker_open_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render an open circuit breaker as ``503 provider_unavailable`` (ADR 0020).

    A distinct type from the auth ``401`` / rate-limit ``429`` shapes, so a client
    can tell "the model provider is temporarily down, retry shortly" apart from
    "your request was wrong". The exception carries only the breaker name (no call
    data), so the log is clean. Fails fast — this is the breaker refusing to wait
    out the SDK's own timeout, not a hang.
    """
    name = exc.name if isinstance(exc, CircuitBreakerOpenError) else "unknown"
    _logger.warning("circuit_breaker.rejected", extra={"breaker": name})
    return _render(503, "provider_unavailable", "The model provider is temporarily unavailable.")


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Catch-all: log the full trace, return a generic 500 (no internals leaked)."""
    _logger.error("unhandled.exception", exc_info=exc)
    return _render(500, "internal_error", "An internal server error occurred.")


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all error handlers to ``app``."""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    # Before the catch-all: an open breaker is a known 503, not an internal 500.
    app.add_exception_handler(CircuitBreakerOpenError, circuit_breaker_open_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
