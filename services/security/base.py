"""The security contracts: authentication and guardrail seams.

**Implemented in Stage 8 (security).** Stage 1 shipped these ABCs as stubs whose
methods raised ``NotImplementedError``; the concrete implementations now live
alongside them in this package:

- :class:`AuthProvider` -> :class:`~services.security.auth.ApiKeyAuthProvider`
  (bearer API-key authentication, ADR 0019).
- :class:`Guardrail` -> :class:`~services.security.guardrails.InjectionPatternGuardrail`
  (a heuristic screen over retrieved excerpts) and
  :class:`~services.security.guardrails.UserInputGuardrail` (a screen over the
  user's own chat input).

The ABCs are the narrow contract callers depend on. Both guardrail
implementations also expose a richer ``screen`` method returning a
:class:`~services.security.guardrails.GuardrailResult`, so a caller that needs
the *flag* (a logged signal) as well as the *block* decision can have it —
``check`` is the boolean projection of that result. See ADR 0019.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AuthProvider(ABC):
    """Authenticates a caller from a bearer credential."""

    @abstractmethod
    async def authenticate(self, credential: str) -> str:
        """Return a principal id for ``credential``, or reject it.

        Raises:
            AuthenticationError: when the credential is unknown or invalid.
        """
        raise NotImplementedError


class Guardrail(ABC):
    """Screens untrusted input/output text (e.g. prompt-injection, PII)."""

    @abstractmethod
    async def check(self, text: str) -> bool:
        """Return ``True`` if ``text`` is allowed, ``False`` if it must be blocked.

        The boolean is intentionally coarse: it says only "block or not". A
        caller that also wants the flagged-but-allowed signal (for telemetry)
        uses the concrete implementation's ``screen`` method, which returns a
        :class:`~services.security.guardrails.GuardrailResult`.
        """
        raise NotImplementedError
