"""Security service: API-key authentication, rate limiting, and guardrails.

**Implemented in Stage 8 (ADR 0019).** The ABCs in :mod:`services.security.base`
are the contract; the concrete implementations live alongside them:

- :class:`~services.security.auth.ApiKeyAuthProvider` — bearer API-key auth.
- :class:`~services.security.rate_limit.RedisRateLimiter` — per-principal,
  Redis-backed, fail-open rate limiting.
- :class:`~services.security.guardrails.InjectionPatternGuardrail` and
  :class:`~services.security.guardrails.UserInputGuardrail` — heuristic screens.
"""

from __future__ import annotations

from services.security.auth import ApiKeyAuthProvider, AuthenticationError, build_auth_provider
from services.security.base import AuthProvider, Guardrail
from services.security.guardrails import (
    GuardrailResult,
    InjectionPatternGuardrail,
    UserInputGuardrail,
)
from services.security.rate_limit import RedisRateLimiter, build_rate_limiter

__all__ = [
    "ApiKeyAuthProvider",
    "AuthProvider",
    "AuthenticationError",
    "Guardrail",
    "GuardrailResult",
    "InjectionPatternGuardrail",
    "RedisRateLimiter",
    "UserInputGuardrail",
    "build_auth_provider",
    "build_rate_limiter",
]
