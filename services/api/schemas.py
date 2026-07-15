"""Request/response schemas for the chat completion API.

The wire format deliberately mirrors OpenAI's chat completion shape (``choices``,
``delta``, ``usage``, ``finish_reason``). That is not cargo-culting: it means
existing clients and SDKs work against this service unchanged, and it is the
format Stage 3's real model backends already speak. See ADR 0004.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]
FinishReason = Literal["stop", "length"]


class ChatMessage(BaseModel):
    """A single turn in the conversation."""

    role: Role
    content: str


class ChatCompletionRequest(BaseModel):
    """A chat completion request."""

    messages: list[ChatMessage] = Field(min_length=1)
    model: str = "agent"
    stream: bool = False
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description=(
            "Accepted for wire compatibility but NOT forwarded to the model: the "
            "Claude 4.7+ family removed sampling parameters and rejects them with "
            "a 400. See ADR 0006."
        ),
    )
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Optional. When set, prior turns of this conversation are loaded from "
            "the server and this turn is persisted. When omitted the request is "
            "stateless and the client owns the history, as in Stage 2."
        ),
    )


class Usage(BaseModel):
    """Token accounting.

    Real counts reported by the model backend from Stage 3 — summed across every
    model call an agent run made, not just the last one. See ADR 0006.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class Choice(BaseModel):
    """One completed alternative."""

    index: int = 0
    message: ChatMessage
    finish_reason: FinishReason


class ChatCompletionResponse(BaseModel):
    """A non-streamed chat completion."""

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage


class Delta(BaseModel):
    """The incremental payload of a streamed chunk.

    ``role`` appears on the first chunk only; ``content`` is absent on the final
    chunk, which carries just the ``finish_reason``.
    """

    role: Role | None = None
    content: str | None = None


class ChunkChoice(BaseModel):
    """One alternative's incremental update."""

    index: int = 0
    delta: Delta
    finish_reason: FinishReason | None = None


class ChatCompletionChunk(BaseModel):
    """One SSE frame of a streamed completion."""

    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChunkChoice]
