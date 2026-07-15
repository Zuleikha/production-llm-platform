"""The orchestration contract, and the agent orchestrator that implements it.

Stage 1 shipped only the ABC; Stage 3 fills it in with
:class:`AgentOrchestrator`, which composes the three things a chat turn needs
and that the agent itself deliberately knows nothing about:

1. conversation history (load prior turns, persist new ones),
2. the agent loop (``services.orchestrator.graph``),
3. real token accounting.

Keeping these here rather than in the agent is what lets Stage 6 run the same
agent under an evaluation harness with no conversation store at all.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shared.logging import get_logger
from shared.observability import traced

from services.orchestrator.conversations import Turn
from services.orchestrator.llm import TokenUsage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from services.orchestrator.conversations import ConversationStore
    from services.orchestrator.graph import AgentGraph

_logger = get_logger("orchestrator.base")


class Orchestrator(ABC):
    """Coordinates a multi-step workflow and returns its final state."""

    @abstractmethod
    async def run(self, request: Mapping[str, object]) -> Mapping[str, object]:
        """Execute the workflow for ``request`` and return the result state."""


@dataclass(frozen=True, slots=True)
class AgentResult:
    """The outcome of one orchestrated turn."""

    answer: str
    usage: TokenUsage
    stop_reason: str | None


class AgentOrchestrator(Orchestrator):
    """Runs one chat turn: load history, run the agent, persist, report usage."""

    def __init__(self, graph: AgentGraph, store: ConversationStore) -> None:
        self._graph = graph
        self._store = store

    async def _prepare(
        self, messages: Sequence[Turn], conversation_id: str | None
    ) -> list[dict[str, Any]]:
        """Build the model's message list: stored history, then this request.

        Prior turns come first so the model reads them in order. A request with
        no ``conversation_id`` is stateless by design — it touches neither
        Postgres nor Redis, which is what keeps the plain OpenAI-shaped call
        (client sends full history) working exactly as it did in Stage 2.
        """
        history: list[dict[str, Any]] = []
        if conversation_id is not None:
            history = list(await self._store.load(conversation_id))
        return [*history, *({"role": m.role, "content": m.content} for m in messages)]

    async def _persist(
        self, conversation_id: str | None, messages: Sequence[Turn], answer: str
    ) -> None:
        """Append this turn's request and reply to the conversation."""
        if conversation_id is None:
            return
        await self._store.append(
            conversation_id,
            [*messages, Turn(role="assistant", content=answer)],
        )

    @traced
    async def answer(
        self,
        messages: Sequence[Turn],
        *,
        conversation_id: str | None = None,
        max_tokens: int | None = None,
    ) -> AgentResult:
        """Run one turn to completion."""
        prepared = await self._prepare(messages, conversation_id)
        state = await self._graph.run(prepared, max_tokens=max_tokens)
        await self._persist(conversation_id, messages, state["answer"])
        _logger.info(
            "agent.answered",
            extra={
                "steps": state["steps"],
                "input_tokens": state["usage"].input_tokens,
                "output_tokens": state["usage"].output_tokens,
                "stop_reason": state["stop_reason"],
            },
        )
        return AgentResult(
            answer=state["answer"], usage=state["usage"], stop_reason=state["stop_reason"]
        )

    async def answer_stream(
        self,
        messages: Sequence[Turn],
        *,
        conversation_id: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str | AgentResult]:
        """Yield answer text as it is produced, then one :class:`AgentResult`.

        Persistence happens after the stream drains — the reply is not a turn
        until it is complete, and half a sentence is not worth storing.

        Not decorated with ``@traced``: async generators are exempt (CLAUDE.md).
        """
        prepared = await self._prepare(messages, conversation_id)
        answer_parts: list[str] = []
        final: AgentResult | None = None

        async for item in self._graph.stream(prepared, max_tokens=max_tokens):
            if isinstance(item, str):
                answer_parts.append(item)
                yield item
            else:
                final = AgentResult(
                    # The answer is what was actually streamed, NOT the final
                    # state's `answer`. On a tool-using run the model emits text
                    # across several turns and the client received all of it;
                    # reporting only the last turn would make the reported answer
                    # disagree with the bytes on the wire — and would break the
                    # engine protocol's "streamed pieces concatenate to the whole
                    # reply" contract.
                    answer="".join(answer_parts),
                    usage=item["usage"],
                    stop_reason=item["stop_reason"],
                )

        if final is None:  # pragma: no cover - the graph always emits final state
            raise RuntimeError("agent graph finished without emitting final state")

        await self._persist(conversation_id, messages, final.answer)
        yield final

    @traced
    async def run(self, request: Mapping[str, object]) -> Mapping[str, object]:
        """Execute the workflow for ``request`` and return the result state.

        The generic ``Orchestrator`` entry point. ``answer`` is the typed path
        the API actually uses; this exists so the ABC's contract is honoured and
        so Stage 6's pipelines have an untyped, mapping-shaped door.

        Raises:
            TypeError: if ``request`` has no usable ``messages`` list.
        """
        raw = request.get("messages")
        if not isinstance(raw, list) or not raw:
            raise TypeError("request must contain a non-empty 'messages' list")
        messages = [
            m if isinstance(m, Turn) else Turn(role=m["role"], content=m["content"]) for m in raw
        ]
        conversation_id = request.get("conversation_id")
        result = await self.answer(
            messages,
            conversation_id=conversation_id if isinstance(conversation_id, str) else None,
        )
        return {
            "answer": result.answer,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "stop_reason": result.stop_reason,
        }
