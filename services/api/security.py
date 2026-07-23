"""Security wiring for the API: bearer auth, per-principal rate limiting, and the
input/egress guardrail hooks the chat route calls.

The auth and rate-limit checks are FastAPI **dependencies** (matching the
``EngineDep`` pattern in ``routes/chat.py``), applied to ``/v1/chat/completions``
only. ``/health``, ``/ready``, ``/version`` and ``/metrics`` are deliberately not
gated (ADR 0019): kubelet's probes and Prometheus's scrape carry no credential.

Every auth failure — missing header, malformed header, wrong key — renders as the
**same** 401 in the uniform error envelope (``services/api/errors.py``). The
response body never says *why*; the log line distinguishes reasons for ops. The
raw credential is never logged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request
from shared.config import Settings
from shared.logging import get_logger
from shared.observability import traced
from starlette.exceptions import HTTPException

from services.retrieval.egress import check_answer_egress
from services.security.auth import AuthenticationError
from services.security.base import AuthProvider
from services.security.guardrails import UserInputGuardrail
from services.security.rate_limit import RedisRateLimiter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from services.api.schemas import ChatMessage

_logger = get_logger("api.security")

_BEARER_PREFIX = "Bearer "


def _bearer_credential(header: str | None) -> str | None:
    """Extract the credential from an ``Authorization: Bearer <key>`` header.

    Returns ``None`` for a missing header, a non-bearer scheme, or an empty
    credential — the caller collapses all of those into the one 401 shape.
    """
    if header is None or not header.startswith(_BEARER_PREFIX):
        return None
    credential = header[len(_BEARER_PREFIX) :].strip()
    return credential or None


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Missing or invalid credentials.")


@traced
async def require_principal(request: Request) -> str:
    """Resolve the authenticated principal id, or raise 401.

    Missing/malformed header and unrecognised key are all the same 401 response;
    only the log distinguishes them.
    """
    provider: AuthProvider = request.app.state.auth_provider
    credential = _bearer_credential(request.headers.get("authorization"))
    if credential is None:
        _logger.info("auth.rejected", extra={"reason": "missing_or_malformed"})
        raise _unauthorized()
    try:
        principal = await provider.authenticate(credential)
    except AuthenticationError:
        _logger.info("auth.rejected", extra={"reason": "invalid_credential"})
        raise _unauthorized() from None
    _logger.info("auth.authenticated", extra={"principal": principal})
    return principal


PrincipalDep = Annotated[str, Depends(require_principal)]


@traced
async def enforce_rate_limit(request: Request, principal: PrincipalDep) -> str:
    """Count this request against the principal's window; raise 429 if over.

    Depends on :func:`require_principal`, so the limiter is keyed by an
    authenticated principal, never an anonymous caller. Fail-open on a Redis
    outage is handled inside the limiter (ADR 0008/0019).
    """
    limiter: RedisRateLimiter = request.app.state.rate_limiter
    if not await limiter.check(principal):
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")
    return principal


# The dependency the chat route depends on: a valid principal that is also within
# its rate-limit window.
AuthorizedPrincipal = Annotated[str, Depends(enforce_rate_limit)]


def screen_user_input(request: Request, principal: str, messages: Sequence[ChatMessage]) -> None:
    """Screen the current user turn before the agent loop runs (ADR 0019).

    Screens the last message — the turn this request contributes. A flagged
    match is always logged (principal + categories, never the text). A *blocked*
    match raises 400 in the uniform envelope; a flagged-but-allowed match passes
    through. Which categories block is the guardrail's call, not the route's.
    """
    guardrail: UserInputGuardrail | None = request.app.state.input_guardrail
    if guardrail is None:
        return
    result = guardrail.screen(messages[-1].content)
    if result.flagged:
        _logger.info(
            "security.input_guardrail",
            extra={
                "principal": principal,
                "blocked": result.blocked,
                "categories": list(result.categories),
            },
        )
    if result.blocked:
        raise HTTPException(status_code=400, detail="Request rejected by input guardrail.")


def screen_answer_egress(request: Request, principal: str, answer: str) -> None:
    """Screen the model's final answer for fence-marker / preamble leakage.

    A logged signal only — never blocks or rewrites (ADR 0019). Runs identically
    for the streamed and non-streamed paths; on the streamed path the bytes have
    already left, which is fine because the decision is log-only either way.
    """
    settings: Settings = request.app.state.settings
    if not settings.egress_guardrail_enabled:
        return
    result = check_answer_egress(answer)
    if result.flagged:
        _logger.info(
            "security.egress_guardrail",
            extra={"principal": principal, "categories": list(result.categories)},
        )
