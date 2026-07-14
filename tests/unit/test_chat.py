"""Tests for the chat/completion endpoints, validation, and SSE streaming.

No real model is called in this stage — the endpoint is backed by a deterministic
echo engine (Stage 3 swaps in the orchestrator). These tests therefore pin the
*contract and the plumbing*: schema validation, the error envelope, the response
shape, and the SSE wire format.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

_ENDPOINT = "/v1/chat/completions"


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


def test_max_tokens_truncates_and_reports_length_finish_reason(client: TestClient) -> None:
    resp = client.post(
        _ENDPOINT,
        json={
            "messages": [{"role": "user", "content": "one two three four five"}],
            "max_tokens": 2,
        },
    )

    body = resp.json()
    assert body["choices"][0]["finish_reason"] == "length"
    assert body["usage"]["completion_tokens"] == 2


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
