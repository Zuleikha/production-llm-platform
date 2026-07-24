"""Tests for the model seam — above all, that the test profile cannot spend money.

The tripwires in :class:`TestTestProfileCannotCallAnthropic` are the ones that
matter. They assert the *mechanism* (ADR 0009), not the intention: a real client
cannot be constructed under the ``test`` profile even when a valid-looking key is
sitting in the environment. If someone later replaces that guard with a
convention ("remember to patch it"), these fail.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from anthropic import APIConnectionError, APITimeoutError, BadRequestError, InternalServerError
from services.orchestrator.llm import (
    PROVIDER_DOWN_ERRORS,
    AnthropicClient,
    AssistantTurn,
    CircuitBreakingLLMClient,
    LLMClient,
    ScriptedLLMClient,
    TextDelta,
    TokenUsage,
    TurnCompleted,
    build_llm_client,
    build_resilient_llm_client,
)
from shared.config import Settings, get_settings
from shared.resilience import CircuitBreaker, CircuitBreakerOpenError, CircuitState

from tests.fakes import FaultInjectingLLMClient

if TYPE_CHECKING:
    from collections.abc import Sequence

    from services.orchestrator.llm import LLMEvent


async def _drain(
    client: LLMClient,
    *,
    system: str = "sys",
    messages: Sequence[dict[str, Any]] | None = None,
) -> list[LLMEvent]:
    return [
        event
        async for event in client.stream(
            system=system,
            messages=messages if messages is not None else [{"role": "user", "content": "hi"}],
            tools=[],
            max_tokens=64,
        )
    ]


class TestTokenUsage:
    def test_sums_across_turns(self) -> None:
        total = TokenUsage(10, 2) + TokenUsage(30, 5)
        assert total == TokenUsage(40, 7)

    def test_defaults_to_zero(self) -> None:
        assert TokenUsage() + TokenUsage() == TokenUsage(0, 0)


class TestTestProfileCannotCallAnthropic:
    """ADR 0009's enforcement, tested as a mechanism rather than a promise."""

    def test_the_real_client_refuses_to_construct_under_the_test_profile(self) -> None:
        settings = Settings(environment="test", anthropic_api_key="sk-ant-looks-real")

        with pytest.raises(RuntimeError, match="never be constructed under the 'test' profile"):
            AnthropicClient(settings)

    def test_the_factory_returns_a_scripted_double_under_the_test_profile(self) -> None:
        settings = Settings(environment="test", anthropic_api_key="sk-ant-looks-real")

        assert isinstance(build_llm_client(settings), ScriptedLLMClient)

    def test_a_key_in_the_real_environment_does_not_change_that(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard keys on the profile, not on the key's absence.

        This is the difference between hermetic and merely-unconfigured: a
        developer with ANTHROPIC_API_KEY exported (which is normal) must not get
        a different, billable test suite from CI's.
        """
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-a-real-looking-key")
        get_settings.cache_clear()

        try:
            settings = get_settings()
            assert settings.anthropic_api_key == "sk-ant-a-real-looking-key"
            assert isinstance(build_llm_client(settings), ScriptedLLMClient)
            with pytest.raises(RuntimeError):
                AnthropicClient(settings)
        finally:
            get_settings.cache_clear()

    def test_the_active_suite_is_running_the_scripted_client(self, settings: Settings) -> None:
        """A tripwire on the fixtures every other test relies on."""
        assert settings.is_test
        assert isinstance(build_llm_client(settings), ScriptedLLMClient)


class TestAnthropicClientConstruction:
    def test_requires_an_api_key_outside_the_test_profile(self) -> None:
        settings = Settings(environment="dev", anthropic_api_key=None)

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is not set"):
            AnthropicClient(settings)

    def test_constructs_in_dev_with_a_key(self) -> None:
        """Constructing dials nothing — no request is made until `stream`."""
        client = AnthropicClient(Settings(environment="dev", anthropic_api_key="sk-ant-x"))

        assert isinstance(client, LLMClient)


class TestScriptedLLMClient:
    async def test_streams_text_then_completes_the_turn(self) -> None:
        turn = AssistantTurn(text="a b c", stop_reason="end_turn")

        events = await _drain(ScriptedLLMClient([turn]))

        assert isinstance(events[-1], TurnCompleted)
        assert events[-1].turn is turn

    async def test_streamed_pieces_reassemble_to_the_turn_text(self) -> None:
        """The protocol contract: transport must not change the answer."""
        events = await _drain(ScriptedLLMClient([AssistantTurn(text="one two three")]))

        streamed = "".join(e.text for e in events if isinstance(e, TextDelta))
        assert streamed == "one two three"

    async def test_default_script_echoes_the_last_user_turn(self) -> None:
        events = await _drain(
            ScriptedLLMClient(),
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "second"},
            ],
        )

        final = events[-1]
        assert isinstance(final, TurnCompleted)
        assert final.turn.text == "You said: second"

    async def test_records_what_it_was_asked(self) -> None:
        client = ScriptedLLMClient([AssistantTurn(text="x")])

        await _drain(client)

        assert client.calls[0]["system"] == "sys"
        assert client.calls[0]["max_tokens"] == 64

    async def test_running_out_of_script_fails_loudly(self) -> None:
        """A silent extra turn would make a scripted test assert nothing."""
        client = ScriptedLLMClient([])

        with pytest.raises(AssertionError, match="ran out of scripted turns"):
            await _drain(client)

    async def test_satisfies_the_protocol(self) -> None:
        assert isinstance(ScriptedLLMClient(), LLMClient)


# --- Prompt caching (Stage 9, ADR 0020) ------------------------------------


class _FakeStream:
    """The async context-manager + async-iterator shape the SDK's stream returns."""

    def __init__(self, final: Any) -> None:
        self._final = final

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration

    async def get_final_message(self) -> Any:
        return self._final


class _SpyMessages:
    """Records the kwargs passed to ``messages.stream`` instead of calling the API."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def stream(self, **kwargs: Any) -> _FakeStream:
        self.kwargs = kwargs
        text_block = SimpleNamespace(
            type="text", text="hi", model_dump=lambda **_: {"type": "text", "text": "hi"}
        )
        final = SimpleNamespace(
            content=[text_block],
            model="claude-opus-4-8",
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )
        return _FakeStream(final)


class _SpyAnthropic:
    def __init__(self) -> None:
        self.messages = _SpyMessages()


def _spied_client() -> tuple[AnthropicClient, _SpyMessages]:
    """A real ``AnthropicClient`` (dev profile) with the SDK swapped for a spy.

    Construction dials nothing; the spy replaces the transport before ``stream``,
    so no network call happens and the test profile's hermetic guard is untouched.
    """
    client = AnthropicClient(Settings(environment="dev", anthropic_api_key="sk-ant-x"))
    spy = _SpyAnthropic()
    client._client = spy  # type: ignore[assignment]
    return client, spy.messages


class TestPromptCaching:
    async def test_system_prompt_is_sent_as_a_cached_content_block(self) -> None:
        client, messages = _spied_client()

        await _drain(client, system="SYSTEM", messages=[{"role": "user", "content": "hi"}])

        assert messages.kwargs is not None
        assert messages.kwargs["system"] == [
            {"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}
        ]

    async def test_only_the_last_tool_spec_is_marked_cacheable(self) -> None:
        client, messages = _spied_client()
        tools = [
            {"name": "a", "description": "A", "input_schema": {}},
            {"name": "b", "description": "B", "input_schema": {}},
        ]

        async for _ in client.stream(
            system="S", messages=[{"role": "user", "content": "hi"}], tools=tools, max_tokens=8
        ):
            pass

        sent = messages.kwargs["tools"]  # type: ignore[index]
        assert "cache_control" not in sent[0]
        assert sent[-1]["cache_control"] == {"type": "ephemeral"}

    async def test_marking_tools_does_not_mutate_the_caller_specs(self) -> None:
        """The registry's tool specs are shared and long-lived — must stay clean."""
        client, _ = _spied_client()
        tools = [{"name": "a", "description": "A", "input_schema": {}}]

        async for _ in client.stream(
            system="S", messages=[{"role": "user", "content": "hi"}], tools=tools, max_tokens=8
        ):
            pass

        assert "cache_control" not in tools[0]

    async def test_no_tools_is_handled(self) -> None:
        client, messages = _spied_client()

        await _drain(client, messages=[{"role": "user", "content": "hi"}])

        assert messages.kwargs["tools"] == []  # type: ignore[index]


# --- Circuit breaker around the LLM client (Stage 9, ADR 0020) --------------


def _provider_breaker(clock: Any, *, threshold: int = 3, cooldown: float = 30.0) -> CircuitBreaker:
    return CircuitBreaker(
        name="test-llm",
        failure_threshold=threshold,
        cooldown_seconds=cooldown,
        trip_on=PROVIDER_DOWN_ERRORS,
        clock=clock,
    )


def _conn_error() -> APIConnectionError:
    return APIConnectionError(message="down", request=httpx.Request("POST", "http://x"))


class _MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestProviderDownErrors:
    def test_timeout_and_5xx_qualify_but_a_400_does_not(self) -> None:
        req = httpx.Request("POST", "http://x")
        assert isinstance(APITimeoutError(request=req), PROVIDER_DOWN_ERRORS)
        assert isinstance(
            InternalServerError("x", response=httpx.Response(500, request=req), body=None),
            PROVIDER_DOWN_ERRORS,
        )
        assert not isinstance(
            BadRequestError("x", response=httpx.Response(400, request=req), body=None),
            PROVIDER_DOWN_ERRORS,
        )


class TestCircuitBreakingLLMClient:
    async def test_passes_through_when_closed(self) -> None:
        inner = FaultInjectingLLMClient(fail_times=0, error=_conn_error())
        wrapped = CircuitBreakingLLMClient(inner, _provider_breaker(_MutableClock()))

        events = await _drain(wrapped)

        assert isinstance(events[-1], TurnCompleted)

    async def test_trips_after_n_failures_then_fails_fast(self) -> None:
        clock = _MutableClock()
        inner = FaultInjectingLLMClient(fail_times=99, error=_conn_error())
        breaker = _provider_breaker(clock, threshold=3)
        wrapped = CircuitBreakingLLMClient(inner, breaker)

        # Three real attempts that each raise the provider error and are recorded.
        for _ in range(3):
            with pytest.raises(APIConnectionError):
                await _drain(wrapped)

        assert breaker.state is CircuitState.OPEN
        # Now open: the next call fails fast WITHOUT touching the inner client.
        calls_before = inner.calls
        with pytest.raises(CircuitBreakerOpenError):
            await _drain(wrapped)
        assert inner.calls == calls_before

    async def test_half_open_trial_recovers_the_breaker(self) -> None:
        clock = _MutableClock()
        # Fail exactly `threshold` times, then succeed.
        inner = FaultInjectingLLMClient(fail_times=3, error=_conn_error())
        breaker = _provider_breaker(clock, threshold=3, cooldown=30)
        wrapped = CircuitBreakingLLMClient(inner, breaker)

        for _ in range(3):
            with pytest.raises(APIConnectionError):
                await _drain(wrapped)
        opened_state = breaker.state
        assert opened_state is CircuitState.OPEN

        clock.now += 30  # cooldown elapses → next call is the half-open trial
        events = await _drain(wrapped)

        assert isinstance(events[-1], TurnCompleted)
        recovered_state = breaker.state
        assert recovered_state is CircuitState.CLOSED

    async def test_a_400_does_not_trip_the_breaker(self) -> None:
        req = httpx.Request("POST", "http://x")
        bad = BadRequestError("bad", response=httpx.Response(400, request=req), body=None)
        inner = FaultInjectingLLMClient(fail_times=99, error=bad)
        breaker = _provider_breaker(_MutableClock(), threshold=2)
        wrapped = CircuitBreakingLLMClient(inner, breaker)

        for _ in range(5):
            with pytest.raises(BadRequestError):
                await _drain(wrapped)

        # A caller-side 400 is never "the provider is down" — breaker stays closed.
        assert breaker.state is CircuitState.CLOSED

    def test_factory_wraps_in_a_breaker(self) -> None:
        wrapped = build_resilient_llm_client(Settings(environment="test"), ScriptedLLMClient())
        assert isinstance(wrapped, CircuitBreakingLLMClient)
        assert isinstance(wrapped, LLMClient)
