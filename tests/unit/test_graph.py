"""Tests for the LangGraph agent loop.

The loop's job is: reason -> select tool -> execute -> observe -> continue or
answer. These tests drive the real graph with a scripted model, so the routing,
the tool dispatch, the step cap and the usage accumulation are all exercised for
real — only the network is absent.
"""

from __future__ import annotations

from typing import Any

import pytest
from services.agents.base import ToolAgent
from services.agents.tools import Tool, ToolRegistry, ToolResult
from services.orchestrator.graph import AgentGraph, window_messages
from services.orchestrator.llm import AssistantTurn, ScriptedLLMClient, TokenUsage


def _tool_use(name: str, arguments: dict[str, Any], *, block_id: str = "toolu_1") -> AssistantTurn:
    """A turn where the model asks for one tool."""
    return AssistantTurn(
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        stop_reason="tool_use",
        raw_content=({"type": "tool_use", "id": block_id, "name": name, "input": arguments},),
    )


def _final(text: str) -> AssistantTurn:
    return AssistantTurn(
        text=text,
        usage=TokenUsage(input_tokens=20, output_tokens=4),
        stop_reason="end_turn",
        raw_content=({"type": "text", "text": text},),
    )


def _user(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]


class ExplodingTool(Tool):
    """A tool with a bug — not a tool the model misused."""

    @property
    def name(self) -> str:
        return "exploding"

    @property
    def description(self) -> str:
        return "Raises."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def run(self, **kwargs: Any) -> ToolResult:
        raise RuntimeError("boom")


class TestLoop:
    async def test_answers_directly_when_no_tool_is_needed(self) -> None:
        graph = AgentGraph(ScriptedLLMClient([_final("Paris.")]))

        state = await graph.run(_user("capital of France?"))

        assert state["answer"] == "Paris."
        assert state["steps"] == 1

    async def test_calls_a_tool_then_answers_from_its_result(self) -> None:
        llm = ScriptedLLMClient([_tool_use("calculator", {"expression": "6*7"}), _final("42")])
        graph = AgentGraph(llm)

        state = await graph.run(_user("what is 6*7?"))

        assert state["answer"] == "42"
        assert state["steps"] == 2
        # The second model call must have been able to observe the tool result.
        second_call_messages = llm.calls[1]["messages"]
        tool_results = [
            block
            for message in second_call_messages
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert tool_results == [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "42"}]

    async def test_accumulates_usage_across_every_model_call(self) -> None:
        graph = AgentGraph(
            ScriptedLLMClient([_tool_use("calculator", {"expression": "1+1"}), _final("2")])
        )

        state = await graph.run(_user("1+1?"))

        assert state["usage"] == TokenUsage(input_tokens=30, output_tokens=9)

    async def test_executes_every_tool_in_a_parallel_request(self) -> None:
        parallel = AssistantTurn(
            stop_reason="tool_use",
            raw_content=(
                {
                    "type": "tool_use",
                    "id": "a",
                    "name": "calculator",
                    "input": {"expression": "1+1"},
                },
                {
                    "type": "tool_use",
                    "id": "b",
                    "name": "calculator",
                    "input": {"expression": "2+2"},
                },
            ),
        )
        llm = ScriptedLLMClient([parallel, _final("done")])

        await AgentGraph(llm).run(_user("add things"))

        results = [
            block
            for message in llm.calls[1]["messages"]
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert [r["content"] for r in results] == ["2", "4"]

    async def test_all_tool_results_go_back_in_one_message(self) -> None:
        """Splitting them trains the model out of parallel tool calls."""
        parallel = AssistantTurn(
            stop_reason="tool_use",
            raw_content=(
                {
                    "type": "tool_use",
                    "id": "a",
                    "name": "calculator",
                    "input": {"expression": "1+1"},
                },
                {
                    "type": "tool_use",
                    "id": "b",
                    "name": "calculator",
                    "input": {"expression": "2+2"},
                },
            ),
        )
        llm = ScriptedLLMClient([parallel, _final("done")])

        await AgentGraph(llm).run(_user("add things"))

        user_messages = [m for m in llm.calls[1]["messages"] if m["role"] == "user"]
        # The original question, plus exactly one message carrying both results.
        assert len(user_messages) == 2
        assert len(user_messages[1]["content"]) == 2


class TestFailureHandling:
    async def test_a_tool_error_is_fed_back_to_the_model_not_raised(self) -> None:
        """A bad argument is recoverable: the model gets told and tries again."""
        llm = ScriptedLLMClient(
            [_tool_use("calculator", {"expression": "1/0"}), _final("undefined")]
        )

        state = await AgentGraph(llm).run(_user("1/0?"))

        assert state["answer"] == "undefined"
        result = llm.calls[1]["messages"][-1]["content"][0]
        assert result["is_error"] is True
        assert "division by zero" in result["content"]

    async def test_an_unknown_tool_is_fed_back_to_the_model(self) -> None:
        llm = ScriptedLLMClient([_tool_use("teleport", {}), _final("sorry")])

        state = await AgentGraph(llm).run(_user("teleport me"))

        assert state["answer"] == "sorry"
        assert llm.calls[1]["messages"][-1]["content"][0]["is_error"] is True

    async def test_a_crashing_tool_does_not_fail_the_run(self) -> None:
        """A bug in our tool must not throw away a recoverable run."""
        llm = ScriptedLLMClient([_tool_use("exploding", {}), _final("recovered")])
        graph = AgentGraph(llm, tools=ToolRegistry([ExplodingTool()]))

        state = await graph.run(_user("go"))

        assert state["answer"] == "recovered"
        assert llm.calls[1]["messages"][-1]["content"][0]["is_error"] is True


class TestStepCap:
    async def test_stops_after_max_steps_even_if_the_model_keeps_asking(self) -> None:
        """A model that loops forever is a bill, not an agent."""
        forever = [_tool_use("calculator", {"expression": "1+1"}) for _ in range(10)]
        graph = AgentGraph(ScriptedLLMClient(forever), max_steps=3)

        state = await graph.run(_user("loop"))

        assert state["steps"] == 3

    async def test_a_capped_run_still_returns_the_last_answer(self) -> None:
        graph = AgentGraph(
            ScriptedLLMClient([_tool_use("calculator", {"expression": "1+1"})]), max_steps=1
        )

        state = await graph.run(_user("loop"))

        assert state["stop_reason"] == "tool_use"


class TestStreaming:
    async def test_streams_text_then_yields_the_final_state(self) -> None:
        graph = AgentGraph(ScriptedLLMClient([_final("hello there")]))

        items = [item async for item in graph.stream(_user("hi"))]

        assert "".join(i for i in items if isinstance(i, str)) == "hello there"
        assert not isinstance(items[-1], str)

    async def test_streamed_text_matches_the_non_streamed_answer(self) -> None:
        """One graph serves both transports — this is what that buys."""
        whole = await AgentGraph(ScriptedLLMClient([_final("same answer")])).run(_user("hi"))

        streamed_graph = AgentGraph(ScriptedLLMClient([_final("same answer")]))
        pieces = [i async for i in streamed_graph.stream(_user("hi")) if isinstance(i, str)]

        assert "".join(pieces) == whole["answer"]

    async def test_streaming_reports_the_same_usage_as_invoking(self) -> None:
        graph = AgentGraph(ScriptedLLMClient([_final("x")]))

        items = [item async for item in graph.stream(_user("hi"))]

        final = items[-1]
        assert not isinstance(final, str)
        assert final["usage"] == TokenUsage(input_tokens=20, output_tokens=4)


class TestMaxTokens:
    async def test_uses_the_per_run_cap_when_given(self) -> None:
        llm = ScriptedLLMClient([_final("x")])

        await AgentGraph(llm, max_tokens=100).run(_user("hi"), max_tokens=7)

        assert llm.calls[0]["max_tokens"] == 7

    async def test_falls_back_to_the_configured_default(self) -> None:
        llm = ScriptedLLMClient([_final("x")])

        await AgentGraph(llm, max_tokens=100).run(_user("hi"))

        assert llm.calls[0]["max_tokens"] == 100


class TestToolAgent:
    async def test_runs_a_task_to_a_final_answer(self) -> None:
        agent = ToolAgent(AgentGraph(ScriptedLLMClient([_final("done")])))

        assert await agent.run("do the thing") == "done"

    async def test_uses_tools_on_the_way(self) -> None:
        agent = ToolAgent(
            AgentGraph(
                ScriptedLLMClient([_tool_use("calculator", {"expression": "8*8"}), _final("64")])
            )
        )

        assert await agent.run("what is 8*8?") == "64"


def _history(pairs: int) -> list[dict[str, Any]]:
    """A conversation of ``pairs`` user/assistant turns, ending on a user turn."""
    messages: list[dict[str, Any]] = []
    for i in range(pairs):
        messages.append({"role": "user", "content": f"q{i}"})
        messages.append({"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]})
    messages.append({"role": "user", "content": "final question"})
    return messages


class TestWindowMessages:
    def test_returns_all_when_within_the_window(self) -> None:
        messages = _history(2)  # 5 messages
        assert window_messages(messages, 40) is messages

    def test_returns_a_trailing_slice_starting_on_a_user_turn(self) -> None:
        messages = _history(10)  # 21 messages
        sent = window_messages(messages, 6)

        assert len(sent) <= 6
        assert sent[0]["role"] == "user"
        assert isinstance(sent[0]["content"], str)
        # Never drops the current (last) turn.
        assert sent[-1] == messages[-1]

    def test_keeps_the_whole_list_when_no_clean_boundary_exists(self) -> None:
        # A long single tool loop: no plain-string user turn in the tail window.
        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]
        for i in range(10):
            messages.append(
                {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "x"}]}
            )
            messages.append(
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}"}]}
            )
        # Window of 4 covers only tool_use/tool_result blocks → no safe boundary.
        assert window_messages(messages, 4) is messages


class TestContextCompaction:
    async def test_only_the_windowed_slice_reaches_the_model(self) -> None:
        llm = ScriptedLLMClient([_final("answer")])
        graph = AgentGraph(llm, context_window_messages=6)
        messages = _history(10)  # 21 messages

        await graph.run(messages)

        sent = llm.calls[0]["messages"]
        assert sent == window_messages(messages, 6)
        assert len(sent) < len(messages)

    async def test_full_history_is_retained_in_state(self) -> None:
        llm = ScriptedLLMClient([_final("answer")])
        graph = AgentGraph(llm, context_window_messages=6)
        messages = _history(10)

        state = await graph.run(messages)

        # State keeps everything: the 21 inputs plus the new assistant turn. The
        # window narrowed only what the model saw, never what is carried/persisted.
        assert len(state["messages"]) == len(messages) + 1
        assert state["messages"][: len(messages)] == messages


class TestSystemPrompt:
    async def test_every_call_carries_the_tool_specifications(self) -> None:
        llm = ScriptedLLMClient([_final("x")])

        await AgentGraph(llm).run(_user("hi"))

        assert {t["name"] for t in llm.calls[0]["tools"]} == {
            "calculator",
            "text_stats",
            "json_query",
        }

    @pytest.mark.parametrize("phrase", ["tool", "concise"])
    async def test_system_prompt_is_sent(self, phrase: str) -> None:
        llm = ScriptedLLMClient([_final("x")])

        await AgentGraph(llm).run(_user("hi"))

        assert phrase in llm.calls[0]["system"]
