"""Tests for the chat/completion endpoints, validation, and SSE streaming.

From Stage 3 the endpoint runs the real LangGraph agent loop — the same graph,
orchestrator, tool dispatch and token accounting production uses. The only
substitution is the Anthropic client itself, which the ``test`` profile replaces
with a scripted double it *cannot* opt out of (ADR 0009). So these tests pin the
contract and the plumbing — schema validation, the error envelope, the response
shape, the SSE wire format — against real machinery rather than a mock engine.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.completions import OrchestratorEngine
from services.orchestrator.base import AgentOrchestrator
from services.orchestrator.conversations import NullConversationStore
from services.orchestrator.graph import AgentGraph
from services.orchestrator.llm import AssistantTurn, ScriptedLLMClient, TokenUsage

_ENDPOINT = "/v1/chat/completions"


def _client_with_turns(settings: Any, *turns: AssistantTurn) -> TestClient:
    """A TestClient whose agent replays exactly ``turns``.

    Everything below the Anthropic client is real: the graph, the tool registry,
    the orchestrator and the route.
    """
    graph = AgentGraph(ScriptedLLMClient(turns))
    engine = OrchestratorEngine(AgentOrchestrator(graph, NullConversationStore()))
    return TestClient(create_app(settings, engine=engine), raise_server_exceptions=False)


def _messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": "hello world"}]


def _sse_frames(body: str) -> list[str]:
    """Split a raw SSE body into the payload of each ``data:`` frame."""
    return [
        line.removeprefix("data:").strip() for line in body.splitlines() if line.startswith("data:")
    ]


def test_chat_completion_returns_the_expected_envelope(client: TestClient) -> None:
    resp = client.post(_ENDPOINT, json={"messages": _messages()})

    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)
    choice = body["choices"][0]
    assert choice["index"] == 0
    assert choice["message"]["role"] == "assistant"
    assert choice["finish_reason"] == "stop"
    assert "hello world" in choice["message"]["content"]


def test_chat_completion_reports_token_usage(client: TestClient) -> None:
    resp = client.post(_ENDPOINT, json={"messages": _messages()})

    usage = resp.json()["usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_chat_completion_echoes_the_last_user_message(client: TestClient) -> None:
    """The engine is a mock: it must reply to the most recent user turn."""
    resp = client.post(
        _ENDPOINT,
        json={
            "messages": [
                {"role": "system", "content": "you are a bot"},
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "second"},
            ]
        },
    )

    content = resp.json()["choices"][0]["message"]["content"]
    assert "second" in content
    assert "first" not in content


def test_finish_reason_is_length_when_the_model_hits_the_token_cap(settings: Any) -> None:
    """`length` means the *model* stopped at max_tokens — not that we truncated.

    Stage 2 asserted the opposite: its mock chopped the reply to `max_tokens`
    whitespace words and called that `length`. That behaviour was an artefact of
    the mock's invented token counting and is gone. Now `max_tokens` is passed to
    the API and the model reports whether it hit the cap, so this test pins the
    stop_reason -> finish_reason mapping that replaced it. See ADR 0006.
    """
    client = _client_with_turns(
        settings,
        AssistantTurn(
            text="one two",
            usage=TokenUsage(input_tokens=11, output_tokens=2),
            stop_reason="max_tokens",
        ),
    )

    resp = client.post(
        _ENDPOINT,
        json={"messages": [{"role": "user", "content": "count to a hundred"}], "max_tokens": 2},
    )

    body = resp.json()
    assert body["choices"][0]["finish_reason"] == "length"
    assert body["usage"]["completion_tokens"] == 2


def test_max_tokens_is_forwarded_to_the_model(settings: Any) -> None:
    """The cap is the API's job now, so it has to actually reach the API."""
    llm = ScriptedLLMClient([AssistantTurn(text="hi", stop_reason="end_turn")])
    engine = OrchestratorEngine(AgentOrchestrator(AgentGraph(llm), NullConversationStore()))

    with TestClient(create_app(settings, engine=engine)) as client:
        client.post(_ENDPOINT, json={"messages": _messages(), "max_tokens": 77})

    assert llm.calls[0]["max_tokens"] == 77


def test_max_tokens_defaults_when_the_request_omits_it(settings: Any) -> None:
    """Anthropic requires max_tokens on every call, so a default must exist."""
    llm = ScriptedLLMClient([AssistantTurn(text="hi", stop_reason="end_turn")])
    engine = OrchestratorEngine(
        AgentOrchestrator(AgentGraph(llm, max_tokens=512), NullConversationStore())
    )

    with TestClient(create_app(settings, engine=engine)) as client:
        client.post(_ENDPOINT, json={"messages": _messages()})

    assert llm.calls[0]["max_tokens"] == 512


def test_usage_reports_the_models_own_counts_not_a_word_count(settings: Any) -> None:
    """The whole point of the stage: usage comes from the backend, not from us."""
    client = _client_with_turns(
        settings,
        AssistantTurn(
            # Two words, but the model says otherwise — and the model wins.
            text="short answer",
            usage=TokenUsage(input_tokens=1234, output_tokens=57),
            stop_reason="end_turn",
        ),
    )

    usage = client.post(_ENDPOINT, json={"messages": _messages()}).json()["usage"]

    assert usage == {"prompt_tokens": 1234, "completion_tokens": 57, "total_tokens": 1291}


def test_usage_sums_every_model_call_in_a_tool_using_run(settings: Any) -> None:
    """An agent run spans several model calls and the caller pays for all of them."""
    client = _client_with_turns(
        settings,
        AssistantTurn(
            tool_calls=(),
            usage=TokenUsage(input_tokens=100, output_tokens=10),
            stop_reason="tool_use",
            raw_content=(
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "calculator",
                    "input": {"expression": "2+2"},
                },
            ),
        ),
        AssistantTurn(
            text="4",
            usage=TokenUsage(input_tokens=200, output_tokens=20),
            stop_reason="end_turn",
        ),
    )

    usage = client.post(_ENDPOINT, json={"messages": _messages()}).json()["usage"]

    assert usage == {"prompt_tokens": 300, "completion_tokens": 30, "total_tokens": 330}


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"messages": []}, id="empty-messages"),
        pytest.param({"messages": [{"role": "wizard", "content": "hi"}]}, id="bad-role"),
        pytest.param({"messages": [{"role": "user"}]}, id="missing-content"),
        pytest.param({}, id="no-messages-key"),
        pytest.param({"messages": _messages(), "max_tokens": 0}, id="zero-max-tokens"),
        pytest.param({"messages": _messages(), "temperature": 9}, id="temperature-out-of-range"),
    ],
)
def test_invalid_requests_return_422_in_the_error_envelope(
    client: TestClient, payload: dict[str, Any]
) -> None:
    resp = client.post(_ENDPOINT, json=payload)

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["type"] == "validation_error"
    assert set(body["error"].keys()) == {"type", "message", "request_id"}


def test_streaming_uses_the_sse_content_type(client: TestClient) -> None:
    resp = client.post(_ENDPOINT, json={"messages": _messages(), "stream": True})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")


def test_streaming_terminates_with_the_done_sentinel(client: TestClient) -> None:
    resp = client.post(_ENDPOINT, json={"messages": _messages(), "stream": True})

    frames = _sse_frames(resp.text)
    assert frames[-1] == "[DONE]"


def test_streaming_frames_are_chat_completion_chunks(client: TestClient) -> None:
    resp = client.post(_ENDPOINT, json={"messages": _messages(), "stream": True})

    frames = _sse_frames(resp.text)
    chunks = [json.loads(f) for f in frames if f != "[DONE]"]
    assert chunks, "expected at least one content chunk"
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    # One id for the whole stream, mirroring the OpenAI wire format.
    assert len({c["id"] for c in chunks}) == 1
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_streaming_reassembles_to_the_non_streamed_content(client: TestClient) -> None:
    """The transport must not change the answer — this is the whole point of the seam."""
    payload = {"messages": _messages()}
    whole = client.post(_ENDPOINT, json=payload).json()["choices"][0]["message"]["content"]

    resp = client.post(_ENDPOINT, json={**payload, "stream": True})
    chunks = [json.loads(f) for f in _sse_frames(resp.text) if f != "[DONE]"]
    streamed = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)

    assert streamed == whole


def test_streaming_emits_the_assistant_role_once(client: TestClient) -> None:
    resp = client.post(_ENDPOINT, json={"messages": _messages(), "stream": True})

    chunks = [json.loads(f) for f in _sse_frames(resp.text) if f != "[DONE]"]
    roles = [c["choices"][0]["delta"].get("role") for c in chunks]
    assert roles[0] == "assistant"
    assert all(r is None for r in roles[1:])
