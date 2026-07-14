"""Single source of truth for the platform version at runtime.

This constant MUST match ``[project].version`` in ``pyproject.toml``. A unit
test (``tests/unit/test_config.py``) asserts they stay in sync so the two never
drift. The project is installed in application mode (``package = false``), so we
cannot rely on ``importlib.metadata`` to read the version.
"""

from __future__ import annotations

__version__ = "0.1.0"


def get_version() -> str:
    """Return the current platform version string."""
    return __version__
