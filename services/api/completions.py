"""The completion engine seam.

This is the **call boundary** between the HTTP layer and whatever actually
produces an answer. Stage 2 ships one implementation, :class:`EchoEngine`, which
returns a deterministic mock; Stage 3 adds an orchestrator-backed engine
(LangGraph) behind the same protocol, so the routes never change.

Keeping the seam here — rather than calling ``services.orchestrator`` directly —
is what lets Stage 2 prove the endpoint, the schemas and the streaming plumbing
against a mock, while the orchestrator stays the untouched stub it should be
until Stage 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from shared.observability import traced

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from services.api.schemas import ChatMessage


def count_tokens(text: str) -> int:
    """Approximate a token count by whitespace-splitting.

    A stand-in, not a tokenizer: Stage 3 reports real counts from the model
    backend. Good enough to prove the ``usage`` contract.
    """
    return len(text.split())


@runtime_checkable
class CompletionEngine(Protocol):
    """Produces an assistant reply for a conversation.

    ``runtime_checkable`` so the route dependency can narrow ``app.state.engine``
    with ``isinstance``. Note this only verifies the methods exist, not their
    signatures — mypy covers the rest statically.
    """

    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int | None) -> str:
        """Return the full reply text."""
        ...

    def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int | None
    ) -> AsyncIterator[str]:
        """Yield the reply in incremental pieces.

        Concatenating every piece must equal ``complete``'s return value for the
        same input — the transport must not change the answer.
        """
        ...


class EchoEngine:
    """A deterministic mock engine: echoes the most recent user turn.

    No model is called. This exists so the endpoint contract, validation and SSE
    framing are real and testable before Stage 3 wires in a model.
    """

    _PREFIX = "You said: "

    @traced
    def _reply_tokens(self, messages: Sequence[ChatMessage], max_tokens: int | None) -> list[str]:
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        tokens = f"{self._PREFIX}{last_user}".split()
        if max_tokens is not None:
            tokens = tokens[:max_tokens]
        return tokens

    @traced
    async def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int | None) -> str:
        return " ".join(self._reply_tokens(messages, max_tokens))

    async def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int | None
    ) -> AsyncIterator[str]:
        """Yield one whitespace-delimited token at a time.

        Not decorated with ``@traced``: async generators are exempt (see
        CLAUDE.md), and the per-token spans would be noise regardless.
        """
        for index, token in enumerate(self._reply_tokens(messages, max_tokens)):
            # Re-attach the separator the split() above removed, so the client's
            # concatenation reproduces `complete` exactly.
            yield token if index == 0 else f" {token}"
