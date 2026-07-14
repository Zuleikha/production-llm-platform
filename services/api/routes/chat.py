"""Chat completion endpoints, streamed and non-streamed.

Both paths run the same :class:`~services.api.completions.CompletionEngine`, so
streaming is purely a transport choice and cannot change the answer. Streaming
uses SSE (``text/event-stream``) with ``data: {json}`` frames terminated by
``data: [DONE]`` — see ADR 0004 for why SSE over chunked JSON or WebSockets.

No model is called in this stage: the engine is a deterministic mock, replaced
in Stage 3.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request
from shared.observability import traced
from starlette.responses import StreamingResponse

from services.api.completions import CompletionEngine, count_tokens
from services.api.schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    Delta,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

router = APIRouter(prefix="/v1", tags=["chat"])

_DONE = "[DONE]"


def get_engine(request: Request) -> CompletionEngine:
    """Return the completion engine the app was built with.

    A dependency so tests (and Stage 3) can swap the engine without touching the
    route.
    """
    engine = request.app.state.engine
    if not isinstance(engine, CompletionEngine):  # pragma: no cover - create_app guarantees type
        raise RuntimeError("completion engine is not initialised")
    return engine


EngineDep = Annotated[CompletionEngine, Depends(get_engine)]


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def _prompt_tokens(messages: Sequence[ChatMessage]) -> int:
    return sum(count_tokens(m.content) for m in messages)


def _finish_reason(completion_tokens: int, max_tokens: int | None) -> str:
    return "length" if max_tokens is not None and completion_tokens >= max_tokens else "stop"


def _sse(payload: str) -> str:
    """Frame a payload as one SSE event."""
    return f"data: {payload}\n\n"


@router.post(
    "/chat/completions",
    summary="Create a chat completion",
    response_model=None,
    responses={
        200: {
            "description": "A completion, or an SSE stream when `stream` is true.",
            "content": {"application/json": {}, "text/event-stream": {}},
        }
    },
)
@traced
async def create_chat_completion(
    payload: ChatCompletionRequest, engine: EngineDep
) -> ChatCompletionResponse | StreamingResponse:
    """Create a chat completion, streamed or whole."""
    if payload.stream:
        return StreamingResponse(
            _stream_completion(payload, engine),
            media_type="text/event-stream",
            headers={
                # Proxies must not buffer or cache a live stream.
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    return await _whole_completion(payload, engine)


async def _whole_completion(
    payload: ChatCompletionRequest, engine: CompletionEngine
) -> ChatCompletionResponse:
    content = await engine.complete(payload.messages, max_tokens=payload.max_tokens)
    completion_tokens = count_tokens(content)
    return ChatCompletionResponse(
        id=_completion_id(),
        created=int(time.time()),
        model=payload.model,
        choices=[
            Choice(
                message=ChatMessage(role="assistant", content=content),
                finish_reason=_finish_reason(completion_tokens, payload.max_tokens),
            )
        ],
        usage=Usage(
            prompt_tokens=_prompt_tokens(payload.messages),
            completion_tokens=completion_tokens,
            total_tokens=_prompt_tokens(payload.messages) + completion_tokens,
        ),
    )


async def _stream_completion(
    payload: ChatCompletionRequest, engine: CompletionEngine
) -> AsyncIterator[str]:
    """Yield the completion as SSE frames, then the ``[DONE]`` sentinel.

    One completion id spans the whole stream. The first chunk carries the role,
    the last carries only ``finish_reason`` — matching the OpenAI wire format.
    """
    completion_id = _completion_id()
    created = int(time.time())
    emitted = 0

    def chunk(delta: Delta, finish_reason: str | None = None) -> str:
        frame = ChatCompletionChunk(
            id=completion_id,
            created=created,
            model=payload.model,
            choices=[ChunkChoice(delta=delta, finish_reason=finish_reason)],
        )
        # exclude_none matches the OpenAI wire format: unset delta fields are
        # omitted rather than sent as null, so the final chunk is `"delta":{}`.
        return _sse(frame.model_dump_json(exclude_none=True))

    first = True
    async for piece in engine.stream(payload.messages, max_tokens=payload.max_tokens):
        emitted += count_tokens(piece)
        yield chunk(Delta(role="assistant" if first else None, content=piece))
        first = False

    yield chunk(Delta(), _finish_reason(emitted, payload.max_tokens))
    yield _sse(_DONE)
