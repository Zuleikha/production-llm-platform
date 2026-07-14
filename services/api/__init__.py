"""The ``api`` service — the Stage 1 foundation FastAPI application."""

from __future__ import annotations

from services.api.app import app, create_app

__all__ = ["app", "create_app"]
