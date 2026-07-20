"""Tier 2: LLM-as-judge scoring of grounded answers. **Costs money.**

This is the opt-in tier (ADR 0017), the same treatment as the live contract test
(ADR 0015): it makes real, billable Anthropic calls and is **never** part of the
default test run or CI. It is invoked only by ``scripts/evaluate.py --judge``,
behind an explicit flag *and* a key, and built from a non-``test`` profile — the
``test`` profile cannot construct a real model client at all (ADR 0009).

What it grades, per case, using the model as judge:

- **Faithfulness** — is the generated answer supported by the retrieved excerpts,
  or does it assert things they do not say?
- **Citation accuracy** — do the excerpt ids the answer leans on actually contain
  what the answer attributes to them?

Both are scored 0.0-1.0 by the judge model and returned as a :class:`JudgeVerdict`.

Everything here that can be tested without the network is a pure function —
:meth:`LLMJudge.build_prompt` and :func:`parse_verdict` — so the prompt shape and
the verdict parsing are covered hermetically. Only :meth:`LLMJudge.score` and
:func:`generate_grounded_answer`, which actually stream from a model, cost
anything, and even those run against the scripted double under ``test``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from shared.logging import get_logger
from shared.observability import traced

from services.orchestrator.llm import TurnCompleted

if TYPE_CHECKING:
    from collections.abc import Sequence

    from services.orchestrator.llm import LLMClient

_logger = get_logger("evaluation.judge")

_ANSWER_SYSTEM: Final[str] = (
    "You answer the user's question using ONLY the numbered excerpts provided. "
    "If the excerpts do not contain the answer, say so plainly rather than "
    "drawing on outside knowledge. Cite the excerpt numbers you rely on."
)

_JUDGE_SYSTEM: Final[str] = (
    "You are a strict evaluator of a retrieval-augmented answer. You are given a "
    "question, the reference excerpts that were retrieved for it, and an answer. "
    "Score two things from 0.0 to 1.0:\n"
    "- faithfulness: is every claim in the answer supported by the excerpts? "
    "1.0 = fully grounded, 0.0 = contradicts or invents beyond them.\n"
    "- citation_accuracy: do the excerpts the answer cites actually support what "
    "it attributes to them? 1.0 = every citation is apt, 0.0 = citations are "
    "wrong or absent.\n"
    'Reply with ONLY a JSON object: {"faithfulness": <float>, '
    '"citation_accuracy": <float>, "reasoning": "<one sentence>"}. No prose '
    "outside the JSON."
)


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    """One judged answer: two 0.0-1.0 scores and the judge's one-line reason."""

    faithfulness: float
    citation_accuracy: float
    reasoning: str


class LLMJudge:
    """Scores a grounded answer for faithfulness and citation accuracy.

    Depends only on the narrow ``LLMClient`` protocol, so it is exercised in tests
    with :class:`~services.orchestrator.llm.ScriptedLLMClient` and never needs a
    network call to prove its prompt-building and parsing are correct.
    """

    def __init__(self, client: LLMClient, *, max_tokens: int = 1024) -> None:
        self._client = client
        self._max_tokens = max_tokens

    @staticmethod
    def build_prompt(query: str, answer: str, excerpts: Sequence[tuple[str, str]]) -> str:
        """Render the judge's user message from the case, its excerpts and answer."""
        rendered = "\n".join(f"[{eid}] {text}" for eid, text in excerpts) or "(no excerpts)"
        return (
            f"QUESTION:\n{query}\n\n"
            f"REFERENCE EXCERPTS:\n{rendered}\n\n"
            f"ANSWER TO EVALUATE:\n{answer}"
        )

    @traced
    async def score(
        self, query: str, answer: str, excerpts: Sequence[tuple[str, str]]
    ) -> JudgeVerdict:
        """Make one model call and parse its verdict. Real call outside ``test``."""
        text = await _drain(
            self._client,
            system=_JUDGE_SYSTEM,
            content=self.build_prompt(query, answer, excerpts),
            max_tokens=self._max_tokens,
        )
        verdict = parse_verdict(text)
        _logger.info(
            "evaluation.judged",
            # Scores only — the query, answer and excerpts are attacker-influenced
            # data and are never logged (CLAUDE.md).
            extra={
                "faithfulness": verdict.faithfulness,
                "citation_accuracy": verdict.citation_accuracy,
            },
        )
        return verdict


@traced
async def generate_grounded_answer(
    client: LLMClient, query: str, excerpts: Sequence[tuple[str, str]], *, max_tokens: int = 512
) -> str:
    """Produce an answer to ``query`` grounded on ``excerpts``. Real call outside ``test``.

    Deliberately a single model turn with no tools: Tier 2 grades answer quality
    given a fixed set of excerpts, so retrieval is done up front and handed in,
    not re-run as an agent tool call. That keeps a judged case to exactly two
    billable calls — this answer, and the judge that scores it.
    """
    rendered = "\n".join(f"[{eid}] {text}" for eid, text in excerpts) or "(no excerpts)"
    return await _drain(
        client,
        system=_ANSWER_SYSTEM,
        content=f"QUESTION:\n{query}\n\nEXCERPTS:\n{rendered}",
        max_tokens=max_tokens,
    )


def parse_verdict(text: str) -> JudgeVerdict:
    """Extract the JSON verdict from the judge's reply.

    Tolerant of the model wrapping the object in prose (takes the first ``{`` to
    the last ``}``), strict about the result: a reply with no JSON object, or
    missing the two scores, raises rather than scoring 0 — a judge that did not
    answer is a broken run, not a failed answer (CLAUDE.md: fail loud).

    Raises:
        ValueError: if no JSON object is present or the required fields are absent
            or non-numeric.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("judge reply contained no JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"judge reply was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("judge reply JSON was not an object")

    return JudgeVerdict(
        faithfulness=_score_field(payload, "faithfulness"),
        citation_accuracy=_score_field(payload, "citation_accuracy"),
        reasoning=str(payload.get("reasoning", "")),
    )


def _score_field(payload: dict[str, object], name: str) -> float:
    """Read and clamp one 0.0-1.0 score, or fail if it is missing/non-numeric."""
    value = payload.get(name)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"judge verdict is missing a numeric '{name}'")
    return max(0.0, min(1.0, float(value)))


async def _drain(client: LLMClient, *, system: str, content: str, max_tokens: int) -> str:
    """Run one streamed turn and return its completed text.

    Not decorated with ``@traced``: this is a thin async coroutine over the
    client's own stream, whose ``TurnCompleted`` the callers above already trace.
    """
    completed = ""
    async for event in client.stream(
        system=system,
        messages=[{"role": "user", "content": content}],
        tools=[],
        max_tokens=max_tokens,
    ):
        if isinstance(event, TurnCompleted):
            completed = event.turn.text
    return completed
