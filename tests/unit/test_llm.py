"""Tests for the model seam — above all, that the test profile cannot spend money.

The tripwires in :class:`TestTestProfileCannotCallAnthropic` are the ones that
matter. They assert the *mechanism* (ADR 0009), not the intention: a real client
cannot be constructed under the ``test`` profile even when a valid-looking key is
sitting in the environment. If someone later replaces that guard with a
convention ("remember to patch it"), these fail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from services.orchestrator.llm import (
    AnthropicClient,
    AssistantTurn,
    LLMClient,
    ScriptedLLMClient,
    TextDelta,
    TokenUsage,
    TurnCompleted,
    build_llm_client,
)
from shared.config import Settings, get_settings

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
