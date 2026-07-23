"""Heuristic guardrails: a second layer on top of ADR 0014's nonce fencing.

These are **defense-in-depth telemetry, not a claim of detection completeness**.
ADR 0014 already rejected a blocklist as the *primary* injection control because
it is trivially evaded and mangles legitimate security-discussion documents. This
module is framed the same honest way: it flags known-bad shapes as a **logged
signal**, and the nonce fence in :mod:`services.retrieval.tool` remains the
load-bearing mechanism, untouched (ADR 0019).

Two concrete :class:`~services.security.base.Guardrail` implementations:

- :class:`InjectionPatternGuardrail` — screens *retrieved excerpts* for
  injection-shaped text. **Never blocks** (never drops an excerpt): the model
  still needs the document to answer questions about it, and a silent drop would
  both break that and hide the signal. It only flags.
- :class:`UserInputGuardrail` — screens the *user's own chat input*. Blocks a
  small, high-confidence set of egregious cases (direct instruction-override /
  system-prompt-extraction attempts) and logs everything else it recognises.

Both expose ``screen`` (the rich result the caller logs) and ``check`` (the
boolean the ABC requires, projected from ``screen``). Neither logs the screened
text — only the event, a boolean, and the matched category names (the standing
"events not sentences, never log attacker-influenced content" rule).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Protocol

from shared.observability import traced

from services.security.base import Guardrail

# --- Pattern tables -------------------------------------------------------
# Compiled once. Each is (category, pattern). Kept deliberately small and
# readable: the point is a legible signal, not an arms race (ADR 0014/0019).

# Direct attempts to subvert the running instructions. Used both as an
# excerpt-injection signal AND as the user-input BLOCK set — a document that
# says this is data (flag only); a user who says this is attacking (block).
_INSTRUCTION_OVERRIDE: Final = re.compile(
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}"
    r"\b(previous|prior|above|earlier|all)\b[^.\n]{0,25}"
    r"\b(instruction|instructions|prompt|prompts|rule|rules|directive|directives)\b",
    re.IGNORECASE,
)
_SYSTEM_PROMPT_PROBE: Final = re.compile(
    r"\b(reveal|show|print|repeat|output|display|leak|tell me)\b[^.\n]{0,30}"
    r"\b(system prompt|your instructions|initial prompt|the prompt above|your rules)\b",
    re.IGNORECASE,
)
# Text shaped like a tool/function invocation trying to ride in through a
# document. Flagged, never blocked.
_TOOL_INVOCATION: Final = re.compile(
    r"(<\s*tool_use\b|\"type\"\s*:\s*\"tool_use\"|\bcall\b[^.\n]{0,20}\btool\b|\bfunction_call\b)",
    re.IGNORECASE,
)

# Persona / role-play jailbreaks. High false-positive rate (a user may legitimately
# *discuss* DAN), so on user input these LOG only.
_JAILBREAK_PERSONA: Final = re.compile(
    r"\b(dan mode|do anything now|developer mode|jailbreak|"
    r"you are now|pretend (you are|to be)|act as (if|though|an?|a ))",
    re.IGNORECASE,
)
# PII-shaped strings in user input. LOG only: a user pasting their own email or
# card is not an attack, and blocking it would harm legitimate use.
_EMAIL: Final = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_CREDIT_CARD: Final = re.compile(r"\b(?:\d[ -]?){13,16}\b")
_SSN: Final = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# The injection-shaped patterns screened over retrieved excerpts.
_EXCERPT_PATTERNS: Final = (
    ("instruction_override", _INSTRUCTION_OVERRIDE),
    ("system_prompt_probe", _SYSTEM_PROMPT_PROBE),
    ("tool_invocation", _TOOL_INVOCATION),
)
# User-input patterns that BLOCK (unambiguous direct attacks).
_INPUT_BLOCK_PATTERNS: Final = (
    ("instruction_override", _INSTRUCTION_OVERRIDE),
    ("system_prompt_probe", _SYSTEM_PROMPT_PROBE),
)
# User-input patterns that only LOG.
_INPUT_LOG_PATTERNS: Final = (
    ("jailbreak_persona", _JAILBREAK_PERSONA),
    ("pii", _EMAIL),
    ("pii", _CREDIT_CARD),
    ("pii", _SSN),
)


@dataclass(frozen=True, slots=True)
class GuardrailResult:
    """The outcome of screening one piece of text.

    ``flagged`` — a pattern matched (a signal worth logging). ``blocked`` — the
    request must be rejected. ``categories`` — which pattern families matched,
    for the log line; never the text itself.
    """

    flagged: bool
    blocked: bool
    categories: tuple[str, ...]


class TextScreen(Protocol):
    """The narrow "screen this text" surface a caller depends on."""

    def screen(self, text: str) -> GuardrailResult:
        """Return what screening ``text`` found, without acting on it."""
        ...


def _dedupe(categories: list[str]) -> tuple[str, ...]:
    """Preserve first-seen order, drop duplicates (``pii`` can match twice)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for category in categories:
        if category not in seen:
            seen.add(category)
            ordered.append(category)
    return tuple(ordered)


class InjectionPatternGuardrail(Guardrail):
    """Screens retrieved excerpts for injection-shaped text. Flags, never blocks."""

    @traced
    def screen(self, text: str) -> GuardrailResult:
        matched = _dedupe([name for name, pattern in _EXCERPT_PATTERNS if pattern.search(text)])
        # blocked is always False by design: this layer is telemetry, and
        # dropping the excerpt would break answering questions about the document
        # (ADR 0014/0019).
        return GuardrailResult(flagged=bool(matched), blocked=False, categories=matched)

    async def check(self, text: str) -> bool:
        return not self.screen(text).blocked


class UserInputGuardrail(Guardrail):
    """Screens the user's chat input. Blocks egregious cases, logs the rest."""

    @traced
    def screen(self, text: str) -> GuardrailResult:
        blocking = [name for name, pattern in _INPUT_BLOCK_PATTERNS if pattern.search(text)]
        logging_only = [name for name, pattern in _INPUT_LOG_PATTERNS if pattern.search(text)]
        categories = _dedupe([*blocking, *logging_only])
        return GuardrailResult(
            flagged=bool(categories),
            blocked=bool(blocking),
            categories=categories,
        )

    async def check(self, text: str) -> bool:
        return not self.screen(text).blocked
