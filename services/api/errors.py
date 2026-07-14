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


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Catch-all: log the full trace, return a generic 500 (no internals leaked)."""
    _logger.error("unhandled.exception", exc_info=exc)
    return _render(500, "internal_error", "An internal server error occurred.")


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all error handlers to ``app``."""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
