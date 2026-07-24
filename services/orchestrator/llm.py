"""The model seam: one protocol, a real Anthropic client, and a scripted double.

Two things shape this module.

**Streaming is the only primitive.** ``LLMClient.stream`` is the single method:
it yields text as the model produces it and finishes with the completed turn.
A non-streaming caller just drains it. Modelling it the other way — a
``complete`` plus a separate ``stream`` — means two request paths that drift,
and a token-accounting bug that only shows up on one of them.

**The test profile cannot reach Anthropic.** Not "does not" — *cannot*.
:class:`AnthropicClient` refuses to construct when the ``test`` profile is
active, before any key is read, so there is no ordering of imports, fixtures or
monkeypatches that turns a unit test into a paid API call. This mirrors how
Stage 2 kept the datastores hermetic (no URL, never dialled) rather than relying
on every test remembering to patch. See ADR 0009.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from anthropic import APIConnectionError, AsyncAnthropic, InternalServerError
from shared.logging import get_logger
from shared.observability import traced
from shared.resilience import CircuitBreaker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from shared.config import Settings

# The Anthropic failures that mean "the provider is down", not "the caller sent a
# bad request". Verified against the pinned anthropic SDK (0.116.0): APITimeoutError
# subclasses APIConnectionError (transport), and InternalServerError is the 5xx
# APIStatusError. A 400 (BadRequestError — e.g. the "no sampling params" rejection,
# ADR 0006) is deliberately absent: it is a caller bug and must not open the
# breaker. See ADR 0020.
PROVIDER_DOWN_ERRORS: tuple[type[Exception], ...] = (APIConnectionError, InternalServerError)

_logger = get_logger("orchestrator.llm")

# Why the model stopped. "tool_use" is the signal the agent loop branches on.
StopReason = Literal["end_turn", "max_tokens", "tool_use", "stop_sequence", "refusal", "pause_turn"]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Real token counts, as reported by the model backend.

    Stage 2 approximated this by splitting on whitespace. These are the
    provider's own numbers — the only ones that match a bill.
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """Sum usage across the turns of one agent run.

        An agent answers with several model calls, and the caller is charged for
        all of them, so the reported usage has to be the total rather than the
        last turn's.
        """
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool the model asked to run."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    """One completed assistant turn."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)
    stop_reason: StopReason | None = None
    # The raw content blocks, kept verbatim so they can be echoed back to the
    # API on the next turn. The API rejects a tool_result whose tool_use block
    # was reconstructed rather than replayed, so this cannot be rebuilt from the
    # fields above.
    raw_content: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class TextDelta:
    """An incremental piece of assistant text."""

    text: str


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    """The terminal event of a stream: the whole turn."""

    turn: AssistantTurn


LLMEvent = TextDelta | TurnCompleted


@runtime_checkable
class LLMClient(Protocol):
    """Produces one assistant turn, streaming its text as it goes."""

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[LLMEvent]:
        """Yield :class:`TextDelta` events, then exactly one :class:`TurnCompleted`.

        ``messages`` are Anthropic-shaped ``{"role", "content"}`` dicts. That is
        the wire format deliberately: tool_use/tool_result content blocks must be
        replayed byte-for-byte, and a lossy domain type in the middle is how you
        break that.
        """
        ...


class AnthropicClient:
    """The real client. Calls the Anthropic API and costs money.

    Cannot be constructed under the ``test`` profile — see the module docstring
    and ADR 0009.
    """

    def __init__(self, settings: Settings) -> None:
        if settings.is_test:
            raise RuntimeError(
                "AnthropicClient must never be constructed under the 'test' profile: "
                "the suite is hermetic by construction and makes no paid API calls. "
                "Use build_llm_client(settings), which returns a scripted double here."
            )
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set; it is required to call the Anthropic API"
            )
        self._model = settings.anthropic_model
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.anthropic_timeout_seconds,
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.close()

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[LLMEvent]:
        """Stream one turn from the Anthropic Messages API.

        Not decorated with ``@traced``: async generators are exempt (CLAUDE.md).

        Note there is deliberately no ``temperature``/``top_p``/``top_k`` here.
        Those parameters were removed on this model family and are rejected with
        a 400 — the request schema still accepts a ``temperature``, but it stops
        at the HTTP boundary. See ADR 0006.

        **Prompt caching (Stage 9, ADR 0020).** The system prompt and the tool
        specifications are static within a run and usually across the turns of one
        conversation, so both carry a ``cache_control`` breakpoint. The conversion
        is contained entirely here: the ``LLMClient`` wire shape is still
        ``system: str`` / ``tools: Sequence[dict]``; ``_with_cache_control`` turns
        the plain system string into Anthropic's content-block form and marks the
        last tool spec, immediately before the SDK call. Verified against the
        pinned SDK (anthropic 0.116.0): ``cache_control`` is a GA first-class field
        on ``TextBlockParam``/``ToolParam`` (``{"type": "ephemeral"}``), needing no
        beta header. A hermetic test can only assert this request *shape*; a real
        cache hit (``cache_read_input_tokens`` > 0) is confirmable only against the
        live API — folded into the opt-in contract test (ADR 0015).
        """
        cached_system, cached_tools = _with_cache_control(system, tools)
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=cached_system,  # type: ignore[arg-type]
            messages=list(messages),  # type: ignore[arg-type]
            tools=cached_tools,  # type: ignore[arg-type]
        ) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield TextDelta(event.delta.text)
            message = await stream.get_final_message()

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                # block.input is typed as object; the API guarantees a JSON
                # object for a tool_use block, but narrow rather than cast.
                arguments = block.input if isinstance(block.input, dict) else {}
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=arguments))

        _logger.info(
            "llm.turn_completed",
            extra={
                "model": message.model,
                "stop_reason": message.stop_reason,
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
                "tool_calls": len(tool_calls),
            },
        )
        yield TurnCompleted(
            AssistantTurn(
                text="".join(text_parts),
                tool_calls=tuple(tool_calls),
                usage=TokenUsage(
                    input_tokens=message.usage.input_tokens,
                    output_tokens=message.usage.output_tokens,
                ),
                stop_reason=message.stop_reason,
                raw_content=tuple(block.model_dump(exclude_none=True) for block in message.content),
            )
        )


class ScriptedLLMClient:
    """A deterministic ``LLMClient`` that replays a fixed script of turns.

    This is what the ``test`` profile runs (ADR 0009). It is real, working code
    rather than a stub: it implements the protocol honestly, streams its text in
    pieces like the real client, and reports usage — so the graph, the tool loop,
    the SSE framing and the token accounting are all exercised for real. The only
    thing it does not do is make a network call.

    The default script answers by echoing the last user turn, which keeps the
    endpoint's behaviour observable without a model.
    """

    _PREFIX = "You said: "

    def __init__(self, turns: Sequence[AssistantTurn] | None = None) -> None:
        self._script = list(turns) if turns is not None else None
        self.calls: list[dict[str, Any]] = []

    def _next_turn(self, messages: Sequence[dict[str, Any]]) -> AssistantTurn:
        if self._script is not None:
            if not self._script:
                raise AssertionError(
                    "ScriptedLLMClient ran out of scripted turns: the agent loop asked "
                    "for more model calls than the test scripted."
                )
            return self._script.pop(0)
        return self._echo_turn(messages)

    def _echo_turn(self, messages: Sequence[dict[str, Any]]) -> AssistantTurn:
        last_user = ""
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                last_user = content
                break
        text = f"{self._PREFIX}{last_user}"
        return AssistantTurn(
            text=text,
            usage=TokenUsage(
                input_tokens=sum(len(str(m.get("content", "")).split()) for m in messages),
                output_tokens=len(text.split()),
            ),
            stop_reason="end_turn",
            raw_content=({"type": "text", "text": text},),
        )

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[LLMEvent]:
        """Replay the next scripted turn, streaming its text word by word."""
        self.calls.append(
            {
                "system": system,
                "messages": list(messages),
                "tools": list(tools),
                "max_tokens": max_tokens,
            }
        )
        turn = self._next_turn(messages)
        for index, word in enumerate(turn.text.split()):
            # Re-attach the separator split() removed, so concatenating the
            # deltas reproduces `turn.text` exactly.
            yield TextDelta(word if index == 0 else f" {word}")
        yield TurnCompleted(turn)


_CACHE_CONTROL: dict[str, Any] = {"type": "ephemeral"}


def _with_cache_control(
    system: str, tools: Sequence[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mark the system prompt and the last tool spec as cacheable (ADR 0020).

    Returns the system as a one-block content list with a ``cache_control``
    breakpoint, and the tools with the *last* spec marked. One breakpoint on the
    final tool caches the whole tool array up to that point — the specs are a
    single static prefix — so there is no need to mark every tool. Pure and
    side-effect free: it copies rather than mutating the caller's dicts, since the
    tool specifications are shared, long-lived registry objects.
    """
    cached_system = [{"type": "text", "text": system, "cache_control": _CACHE_CONTROL}]
    tool_list = list(tools)
    if tool_list:
        tool_list = [*tool_list[:-1], {**tool_list[-1], "cache_control": _CACHE_CONTROL}]
    return cached_system, tool_list


class CircuitBreakingLLMClient:
    """Wraps an :class:`LLMClient` in a :class:`~shared.resilience.CircuitBreaker`.

    Implements the same protocol, so :class:`~services.orchestrator.graph.AgentGraph`
    takes it as a drop-in for the real client. When the breaker is open the
    ``stream`` raises :class:`~shared.resilience.CircuitBreakerOpenError` before the
    wrapped client is touched — the API renders that as ``503 provider_unavailable``
    (ADR 0020) rather than letting every request wait out the SDK's own timeout.

    A stream that raises a qualifying provider error (transport/5xx) records a
    failure; a clean drain records a success. A non-qualifying error (a 400) is
    re-raised without counting — the breaker only trips on the provider being down.
    """

    def __init__(self, inner: LLMClient, breaker: CircuitBreaker) -> None:
        self._inner = inner
        self._breaker = breaker

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[LLMEvent]:
        """Gate the call on the breaker, then delegate — recording the outcome.

        Not decorated with ``@traced``: async generators are exempt (CLAUDE.md).
        The breaker's own ``before_call``/``record_*`` methods carry the spans.
        """
        self._breaker.before_call()
        try:
            async for event in self._inner.stream(
                system=system, messages=messages, tools=tools, max_tokens=max_tokens
            ):
                yield event
        except GeneratorExit:
            # The consumer stopped early — not a provider failure. Propagate
            # without recording an outcome either way.
            raise
        except BaseException as exc:
            self._breaker.record_failure(exc)
            raise
        else:
            self._breaker.record_success()


@traced
def build_resilient_llm_client(settings: Settings, inner: LLMClient) -> LLMClient:
    """Wrap ``inner`` in a circuit breaker configured from settings (ADR 0020).

    Wired at the one place ``build_llm_client`` is consumed for serving
    (``services.api.app``), so the breaker sits between the graph and the real
    client. Left off the ``build_llm_client`` factory itself deliberately: that
    factory's contract is "the raw client the profile may use", and a test that
    asserts it returns a bare ``ScriptedLLMClient`` must keep passing.
    """
    breaker = CircuitBreaker(
        name="anthropic_llm",
        failure_threshold=settings.circuit_breaker_failure_threshold,
        cooldown_seconds=settings.circuit_breaker_cooldown_seconds,
        trip_on=PROVIDER_DOWN_ERRORS,
    )
    return CircuitBreakingLLMClient(inner, breaker)


@traced
def build_llm_client(settings: Settings) -> LLMClient:
    """Return the LLM client the active profile is allowed to use.

    This is the enforcement point named in ADR 0009: under ``test`` it returns a
    scripted double and the real client is never constructed. It is belt and
    braces with :class:`AnthropicClient`'s own refusal — a test that reaches past
    this factory still cannot dial out.
    """
    if settings.is_test:
        _logger.info("llm.client_selected", extra={"client": "scripted", "reason": "test profile"})
        return ScriptedLLMClient()
    _logger.info(
        "llm.client_selected", extra={"client": "anthropic", "model": settings.anthropic_model}
    )
    return AnthropicClient(settings)
