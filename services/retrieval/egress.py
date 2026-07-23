"""Answer-egress check: the second Stage 8 RAG-hardening addition (ADR 0019).

Before the model's final answer reaches the client, check whether it leaks the
retrieval fence markers or reproduces the untrusted-data preamble verbatim.
Either is a concrete signal that something unexpected happened during generation
— the model was not supposed to echo either back. This is a **logged signal
only**: it never rewrites or blocks the answer (fail-loud is scoped to
"log and optionally reject" here, and the decision for egress is log-only).

It lives in the retrieval package, not ``services/security``, because it must
reference retrieval's own fence-marker format and preamble
(:data:`services.retrieval.tool.PREAMBLE_SIGNATURE`); keeping it here co-locates
it with the ADR 0014 mechanism it extends and avoids a security<->retrieval
import cycle. It reuses :class:`~services.security.guardrails.GuardrailResult`
so a caller logs it exactly like the guardrail screens.

The check matches the fence-marker *stem* rather than a specific nonce: the
per-call nonce (:func:`secrets.token_hex`, ADR 0014) is generated inside the tool
and not plumbed out here, and any excerpt-fence-shaped token in the answer is the
signal regardless of which call produced it.
"""

from __future__ import annotations

import re
from typing import Final

from shared.observability import traced

from services.retrieval.tool import PREAMBLE_SIGNATURE
from services.security.guardrails import GuardrailResult

_FENCE_MARKER: Final = re.compile(r"</?excerpt-[0-9a-f]{8,}")


@traced
def check_answer_egress(answer: str) -> GuardrailResult:
    """Return whether ``answer`` leaks fence markers or echoes the preamble.

    Never blocks (``blocked`` is always ``False``): a logged signal, not a
    rewrite. Returns a :class:`GuardrailResult` so the caller logs it the same
    way it logs the input/excerpt screens.
    """
    categories: list[str] = []
    if _FENCE_MARKER.search(answer):
        categories.append("nonce_fence_leak")
    if PREAMBLE_SIGNATURE in answer:
        categories.append("preamble_echo")
    return GuardrailResult(flagged=bool(categories), blocked=False, categories=tuple(categories))
