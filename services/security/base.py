"""Interface stub: security contract.

**Planned, not yet implemented — Stage 8 (security hardening).**

Defines the authentication and guardrail seams. Concrete implementations
(auth providers, input/output guardrails, rate limiting, secret management)
arrive in Stage 8. See ``README.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AuthProvider(ABC):
    """Authenticates a caller from a bearer credential."""

    @abstractmethod
    async def authenticate(self, credential: str) -> str:
        """Return a principal id for ``credential`` or reject it.

        Raises:
            NotImplementedError: Always, until implemented in Stage 8.
        """
        raise NotImplementedError


class Guardrail(ABC):
    """Screens untrusted input/output text (e.g. prompt-injection, PII)."""

    @abstractmethod
    async def check(self, text: str) -> bool:
        """Return ``True`` if ``text`` is allowed, ``False`` if it must be blocked.

        Raises:
            NotImplementedError: Always, until implemented in Stage 8.
        """
        raise NotImplementedError
