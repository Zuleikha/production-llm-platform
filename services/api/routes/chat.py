"""Chat completion endpoints, streamed and non-streamed.

Both paths run the same :class:`~services.api.completions.CompletionEngine`, so
streaming is purely a transport choice and cannot change the answer. Streaming
uses SSE (``text/event-stream``) with ``data: {json}`` frames terminated by
``data: [DONE]`` — see ADR 0004 for why SSE over chunked JSON or WebSockets.

From Stage 3 the engine runs a real LangGraph agent loop against the Anthropic
API. The wire format is unchanged from Stage 2; what changed is that ``usage``
now carries the model's own token counts instead of a whitespace-split estimate
computed here. That estimate could never have been right for an agent run, which
spends tokens across several model calls this layer never sees. See ADR 0006.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request
from shared.observability import traced
from starlette.responses import StreamingResponse

from services.api.completions import Completion, CompletionEngine
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
    from collections.abc import AsyncIterator

router = APIRouter(prefix="/v1", tags=["chat"])

_DONE = "[DONE]"


def get_engine(request: Request) -> CompletionEngine:
    """Return the completion engine the app was built with.

    A dependency so tests (and later stages) can swap the engine without
    touching the route.
    """
    engine = request.app.state.engine
    if not isinstance(engine, CompletionEngine):  # pragma: no cover - create_app guarantees type
        raise RuntimeError("completion engine is not initialised")
    return engine


EngineDep = Annotated[CompletionEngine, Depends(get_engine)]


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def _usage(completion: Completion) -> Usage:
    """Render the engine's real token counts into the wire format."""
    return Usage(
        prompt_tokens=completion.usage.input_tokens,
        completion_tokens=completion.usage.output_tokens,
        total_tokens=completion.usage.input_tokens + completion.usage.output_tokens,
    )


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
    completion = await engine.complete(
        payload.messages,
        max_tokens=payload.max_tokens,
        conversation_id=payload.conversation_id,
    )
    return ChatCompletionResponse(
        id=_completion_id(),
        created=int(time.time()),
        model=payload.model,
        choices=[
            Choice(
                message=ChatMessage(role="assistant", content=completion.text),
                finish_reason=completion.finish_reason,
            )
        ],
        usage=_usage(completion),
    )


async def _stream_completion(
    payload: ChatCompletionRequest, engine: CompletionEngine
) -> AsyncIterator[str]:
    """Yield the completion as SSE frames, then the ``[DONE]`` sentinel.

    One completion id spans the whole stream. The first chunk carries the role,
    the last carries only ``finish_reason`` — matching the OpenAI wire format.

    The engine's terminal :class:`Completion` supplies the finish reason. It
    arrives only after the last text piece, which is exactly why the final frame
    is emitted after the loop rather than guessed during it.
    """
    completion_id = _completion_id()
    created = int(time.time())

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
    final: Completion | None = None
    async for event in engine.stream(
        payload.messages,
        max_tokens=payload.max_tokens,
        conversation_id=payload.conversation_id,
    ):
        if isinstance(event, Completion):
            final = event
            continue
        yield chunk(Delta(role="assistant" if first else None, content=event))
        first = False

    if final is None:  # pragma: no cover - the engine protocol guarantees one
        raise RuntimeError("engine stream finished without a final completion")

    yield chunk(Delta(), final.finish_reason)
    yield _sse(_DONE)
