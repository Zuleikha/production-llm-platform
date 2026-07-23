#!/usr/bin/env python
"""Generate an API key + its stored hash for the Stage 8 bearer auth (ADR 0019).

Operators issue keys out-of-band. This wraps ``services.security.auth.hash_key``
-- the exact function the auth provider uses at verification time -- so a
generated key's stored hash always matches what the running service expects.

It:
  1. takes a principal id (who/what this key belongs to),
  2. generates a random raw key with ``secrets.token_urlsafe(32)``,
  3. computes its stored hash using the real ``hash_key`` function, keyed by
     the server-side pepper (``API_KEY_HASH_SECRET``),
  4. prints the raw key ONCE -- give it to the caller; it is never stored and
     cannot be recovered from the hash, and
  5. prints the ``principal:hexhash`` pair to append to the ``API_KEYS`` env var.

The pepper is read from ``API_KEY_HASH_SECRET`` in the environment -- the same
value the running service must have, or a key generated here will never
verify. This script fails loudly if that variable is unset rather than
silently using a default, since a mismatched pepper produces keys that look
valid but never authenticate.

The raw key is printed to stdout only. It is never logged, never written to a
file, and never stored anywhere by this script.

Usage:
    API_KEY_HASH_SECRET=... uv run python scripts/generate_api_key.py alice
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

# Allow running this script directly (uv run python scripts/generate_api_key.py)
# without the project needing to be an installed/editable package on sys.path.
# scripts/ sits one level below the repo root, where `services` and `shared`
# live -- insert that root explicitly rather than assuming install state.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.security.auth import hash_key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "principal",
        help="Identifier for who/what this key belongs to (e.g. a service or user name).",
    )
    args = parser.parse_args()

    pepper = os.environ.get("API_KEY_HASH_SECRET")
    if not pepper:
        print(
            "ERROR: API_KEY_HASH_SECRET is not set in the environment.\n"
            "This must match the value the running service uses, or the key\n"
            "generated here will never authenticate. Refusing to guess or\n"
            "default it.",
            file=sys.stderr,
        )
        return 1

    raw_key = secrets.token_urlsafe(32)
    hashed = hash_key(pepper, raw_key)

    print("=" * 70)
    print(f"Raw key for {args.principal!r} -- copy this now, it will not be shown again:")
    print()
    print(f"    {raw_key}")
    print()
    print("Give this to the caller out-of-band (e.g. a secrets manager, not chat/email).")
    print("=" * 70)
    print()
    print("Add this to the API_KEYS env var (comma-separate if there are others):")
    print()
    print(f"    {args.principal}:{hashed}")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
