"""The ``api`` service — the FastAPI application.

Deliberately does **not** import ``app`` here. A package ``__init__`` that
constructs the ASGI application means importing *any* submodule
(``services.api.schemas``, say) builds the whole app and every dependency it
touches — which made a plain import of ``services.orchestrator`` fail with a
circular import. Import what you need directly:

    from services.api.app import app, create_app
"""

from __future__ import annotations

__all__: list[str] = []
