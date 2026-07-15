"""The completion engine seam.

This is the **call boundary** between the HTTP layer and whatever actually
produces an answer. Stage 2 shipped one implementation, ``EchoEngine``, backed by
no model at all; Stage 3 replaces it with :class:`OrchestratorEngine`, which runs
the real LangGraph agent loop against the Anthropic API. The protocol is what
made that swap possible without redesigning the endpoint.

The protocol changed in one way this stage, and it is the point of the stage:
:class:`Completion` now carries **real** token counts from the model backend.
Stage 2 had the *route* approximate usage by splitting the reply on whitespace,
which meant the numbers were invented at the wrong layer and could never match a
bill. Only the engine knows what a turn actually cost — an agent run spans
several model calls the route cannot see — so usage is reported by the engine and
the route reads it. See ADR 0006.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from shared.observability import traced

from services.orchestrator.conversations import Turn
from services.orchestrator.llm import TokenUsage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from services.api.schemas import ChatMessage, FinishReason
    from services.orchestrator.base import AgentOrchestrator


@dataclass(frozen=True, slots=True)
class Completion:
    """A finished reply and what it cost."""

    text: str
    usage: TokenUsage
    finish_reason: FinishReason


# A stream is many text pieces followed by exactly one Completion. Expressed as a
# union rather than two methods so a caller cannot forget to collect the usage.
StreamEvent = str | Completion


@runtime_checkable
class CompletionEngine(Protocol):
    """Produces an assistant reply for a conversation.

    ``runtime_checkable`` so the route dependency can narrow ``app.state.engine``
    with ``isinstance``. Note this only verifies the methods exist, not their
    signatures — mypy covers the rest statically.
    """

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None,
        conversation_id: str | None = None,
    ) -> Completion:
        """Return the whole reply."""
        ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None,
        conversation_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield the reply in pieces, then one final :class:`Completion`.

        Concatenating every text piece must equal the final ``Completion.text``
        — the transport must not change the answer.
        """
        ...


def _finish_reason(stop_reason: str | None) -> FinishReason:
    """Map the model's stop reason onto the OpenAI-shaped wire value.

    The wire format only has ``stop`` and ``length``; ``max_tokens`` is the one
    that means the reply was cut short. Everything else — including a tool_use
    stop that survived the agent loop's step cap — is a completed turn from the
    client's point of view.
    """
    return "length" if stop_reason == "max_tokens" else "stop"


class OrchestratorEngine:
    """The real engine: a LangGraph agent loop behind the Stage 2 protocol.

    Deliberately thin. It exists to translate between the API's vocabulary
    (``ChatMessage``, ``finish_reason``) and the orchestrator's (``Turn``,
    ``stop_reason``), and does no reasoning of its own — every decision worth
    testing lives in the graph.

    This translation is the whole reason the orchestrator does not import the
    API's schemas: the dependency runs api -> orchestrator, and converting here
    is what keeps it that way.
    """

    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        self._orchestrator = orchestrator

    @staticmethod
    def _turns(messages: Sequence[ChatMessage]) -> list[Turn]:
        return [Turn(role=m.role, content=m.content) for m in messages]

    @traced
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None,
        conversation_id: str | None = None,
    ) -> Completion:
        result = await self._orchestrator.answer(
            self._turns(messages), conversation_id=conversation_id, max_tokens=max_tokens
        )
        return Completion(
            text=result.answer,
            usage=result.usage,
            finish_reason=_finish_reason(result.stop_reason),
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None,
        conversation_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream the reply.

        Not decorated with ``@traced``: async generators are exempt (CLAUDE.md).
        """
        async for item in self._orchestrator.answer_stream(
            self._turns(messages), conversation_id=conversation_id, max_tokens=max_tokens
        ):
            if isinstance(item, str):
                yield item
            else:
                yield Completion(
                    text=item.answer,
                    usage=item.usage,
                    finish_reason=_finish_reason(item.stop_reason),
                )
