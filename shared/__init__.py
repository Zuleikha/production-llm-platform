"""Shared foundation: configuration, structured logging, observability, version.

Cross-cutting building blocks imported by every service. Business logic does
NOT live here.
"""

from __future__ import annotations

from shared.config import Settings, get_settings
from shared.logging import get_logger, get_request_id, set_request_id, setup_logging
from shared.observability import traced
from shared.version import __version__, get_version

__all__ = [
    "Settings",
    "__version__",
    "get_logger",
    "get_request_id",
    "get_settings",
    "get_version",
    "set_request_id",
    "setup_logging",
    "traced",
]
