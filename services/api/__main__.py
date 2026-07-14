"""Console entrypoint: ``python -m services.api``.

Starts uvicorn bound to the configured host/port. Reload is enabled only when
the ``debug`` flag is set (dev profile).
"""

from __future__ import annotations

import uvicorn
from shared.config import get_settings
from shared.observability import traced


@traced
def main() -> None:
    """Launch the API service with uvicorn using the active settings."""
    settings = get_settings()
    uvicorn.run(
        "services.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
        log_config=None,  # our own structured logging is configured in create_app
    )


if __name__ == "__main__":
    main()
