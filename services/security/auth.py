"""API-key authentication: bearer keys mapped to a principal id.

The mechanism is deliberately small (ADR 0019): a static, out-of-band-issued API
key presented as ``Authorization: Bearer <key>``, mapped to a principal id. There
is no issuer, no expiry, no external IdP — this project has none, and JWT
issuing/rotation machinery is its own scope, not a one-stage add-on.

**Only a salted hash of each key is ever stored, never the raw value.** The store
holds ``principal -> HMAC-SHA256(secret, key)`` pairs; on authentication the
presented credential is hashed the same way and compared with
:func:`hmac.compare_digest` against every stored hash. The server-side ``secret``
is the salt/pepper — a keyed hash rather than a bare digest, so a leaked store is
not a rainbow-table-able list of key digests. The raw key never touches a log, a
committed file, or an exception message (the standing PII/secret logging rule,
`docs/coding-standards.md`).

AuthZ is single-tier (ADR 0019): a valid key is a valid principal with full
access to the chat endpoint. There is no role/scope split — the API has exactly
one data-bearing endpoint family, and inventing a second capability tier just to
exercise RBAC would be scope creep. Revisit if an admin/ops endpoint is ever
added.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING

from shared.logging import get_logger
from shared.observability import traced

from services.security.base import AuthProvider

if TYPE_CHECKING:
    from shared.config import Settings

_logger = get_logger("security.auth")


class AuthenticationError(Exception):
    """Raised when a credential is missing, malformed, or unrecognised.

    Carries no detail about *why* it failed: the caller renders every failure as
    the same 401 so a client cannot distinguish a wrong key from a malformed
    header from an unknown principal.
    """


def hash_key(secret: str, key: str) -> str:
    """Return the stored form of ``key``: ``HMAC-SHA256(secret, key)`` as hex.

    Exposed (not private) because operators issuing a key compute its stored
    hash with exactly this function, and a test pins that the two agree.
    """
    return hmac.new(secret.encode("utf-8"), key.encode("utf-8"), hashlib.sha256).hexdigest()


def parse_key_store(raw: str) -> dict[str, str]:
    """Parse the ``API_KEYS`` env format into a ``principal -> hash`` mapping.

    The format is comma-separated ``principal:hexhash`` pairs, e.g.
    ``alice:ab12...,bob:cd34...``. A flat string rather than JSON so it sets
    cleanly as a single environment variable with no quoting or escaping — the
    same reasoning the datastore URLs follow (ADR 0003). Whitespace around
    entries is tolerated; a malformed entry fails loudly rather than being
    silently dropped, so a typo cannot quietly lock everyone out.
    """
    store: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        principal, sep, digest = entry.partition(":")
        principal, digest = principal.strip(), digest.strip()
        if not sep or not principal or not digest:
            raise ValueError(
                "API_KEYS entries must be 'principal:hexhash'; "
                f"got a malformed entry ({len(entry)} chars)"
            )
        store[principal] = digest
    return store


class ApiKeyAuthProvider(AuthProvider):
    """Authenticates a bearer key against a ``principal -> salted-hash`` store."""

    def __init__(self, key_store: dict[str, str], *, hash_secret: str) -> None:
        self._key_store = dict(key_store)
        self._hash_secret = hash_secret

    @property
    def principal_count(self) -> int:
        """How many principals are configured. For a boot log, never the keys."""
        return len(self._key_store)

    @traced
    async def authenticate(self, credential: str) -> str:
        """Return the principal id for ``credential`` or raise.

        Constant-time per comparison via :func:`hmac.compare_digest`. Every
        stored hash is checked (rather than breaking on the first match) so the
        work — and therefore the timing — does not depend on which principal, if
        any, the credential belongs to. The raw ``credential`` is never logged.

        Raises:
            AuthenticationError: when no configured key matches.
        """
        presented = hash_key(self._hash_secret, credential)
        matched: str | None = None
        for principal, stored in self._key_store.items():
            if hmac.compare_digest(presented, stored):
                matched = principal
        if matched is None:
            raise AuthenticationError("unrecognised credential")
        return matched


@traced
def build_auth_provider(settings: Settings) -> ApiKeyAuthProvider:
    """Assemble the auth provider from configuration.

    No hermetic seam is needed (unlike the LLM/embeddings clients): auth is local
    logic with no paid external hop, so ``test`` constructs it for real. Under
    ``prod`` the settings validator has already guaranteed both fields are set;
    under ``dev``/``test`` an unset store yields a provider that rejects every
    credential — fail-closed by default, which is the safe direction for auth.
    """
    store = parse_key_store(settings.api_keys or "")
    provider = ApiKeyAuthProvider(store, hash_secret=settings.api_key_hash_secret or "")
    _logger.info("auth.provider_built", extra={"principals": provider.principal_count})
    return provider
