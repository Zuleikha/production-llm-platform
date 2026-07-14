"""Structured logging for the platform.

Produces one JSON object per log line with the fields required by the platform
logging contract: ``timestamp``, ``request_id``, ``service``, ``environment``,
``level``, ``message`` and (on errors) an ``exception`` trace. A human-readable
``console`` formatter is available for local development.

The request id is carried on a :class:`~contextvars.ContextVar` so it is
attached automatically to every log emitted while handling a request, without
threading it through call sites.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shared.config import Settings

# Populated by the request-id middleware; None outside of a request scope.
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes always present on a LogRecord; anything else the caller passed via
# ``extra=`` is treated as a structured field and merged into the JSON payload.
_RESERVED_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


def set_request_id(value: str | None) -> None:
    """Bind ``value`` as the request id for the current context."""
    _request_id.set(value)


def get_request_id() -> str | None:
    """Return the request id bound to the current context, if any."""
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON matching the logging contract."""

    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "environment": self._environment,
            "request_id": get_request_id(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exception"] = record.exc_text
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Compact, human-readable formatter for local development."""

    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        ts = dt.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        rid = get_request_id()
        rid_part = f" rid={rid}" if rid else ""
        base = (
            f"{ts} {record.levelname:<8} [{self._service}:{self._environment}]"
            f"{rid_part} {record.name}: {record.getMessage()}"
        )
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def setup_logging(settings: Settings) -> None:
    """Configure the root logger for the given settings.

    Idempotent: replaces existing handlers so repeated calls (e.g. reload in
    dev, or per-test setup) do not duplicate output. Uvicorn's own loggers are
    routed through our formatter so all output shares one structured format.
    """
    if settings.log_format == "json":
        formatter: logging.Formatter = JsonFormatter(
            service=settings.service_name, environment=settings.environment
        )
    else:
        formatter = ConsoleFormatter(
            service=settings.service_name, environment=settings.environment
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    for name in ("uvicorn", "uvicorn.error"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.propagate = True

    # RequestContextMiddleware already emits a structured `http.request` access
    # log carrying the request id. Uvicorn's own access line duplicates it
    # without correlation (it runs outside the middleware's context), so drop it
    # to WARNING and keep only its errors.
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers.clear()
    uvicorn_access.propagate = True
    uvicorn_access.setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger."""
    return logging.getLogger(name)
