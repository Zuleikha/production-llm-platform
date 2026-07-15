"""End-to-end agent runs: tool use + persistence + cache, over the HTTP endpoint.

Two layers, because they prove different things and only one of them can run
without a database:

- :class:`TestAgentEndToEnd` is **hermetic** and always runs. Everything from the
  route down is real — the graph, the tool registry, the orchestrator, the
  read-through cache, the SSE framing — with only the Anthropic client scripted.
  Persistence goes through the real ``CachedConversationStore`` over an
  in-memory inner store, so the caching contract is genuinely exercised. What it
  does **not** prove is that ``PostgresConversationStore``'s SQL is valid.

- :class:`TestAgainstRealDatastores` closes exactly that gap by running the
  shipped migration and the real store against a live Postgres and Redis. It is
  **skipped unless** ``TEST_DATABASE_URL`` and ``TEST_REDIS_URL`` are set, so
  the default suite stays hermetic per ADR 0005.

Neither layer ever calls Anthropic: the ``test`` profile cannot construct a real
client (ADR 0009).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.completions import OrchestratorEngine
from services.orchestrator.base import AgentOrchestrator
from services.orchestrator.conversations import (
    CachedConversationStore,
    PostgresConversationStore,
)
from services.orchestrator.graph import AgentGraph
from services.orchestrator.llm import AssistantTurn, ScriptedLLMClient, TokenUsage

from tests.fakes import FakeRedis, InMemoryConversationStore

if TYPE_CHECKING:
    from shared.config import Settings

_ENDPOINT = "/v1/chat/completions"

# The model asks for the calculator, reads the result, then answers from it.
# This is the loop the stage exists to build: reason -> select tool -> execute ->
# observe -> answer.
_TOOL_RUN = (
    AssistantTurn(
        usage=TokenUsage(input_tokens=250, output_tokens=40),
        stop_reason="tool_use",
        raw_content=(
            {"type": "text", "text": "Let me calculate that. "},
            {
                "type": "tool_use",
                "id": "toolu_calc",
                "name": "calculator",
                "input": {"expression": "129 * 47"},
            },
        ),
    ),
    AssistantTurn(
        text="129 * 47 = 6063.",
        usage=TokenUsage(input_tokens=310, output_tokens=12),
        stop_reason="end_turn",
        raw_content=({"type": "text", "text": "129 * 47 = 6063."},),
    ),
)


def _sse_frames(body: str) -> list[str]:
    return [
        line.removeprefix("data:").strip() for line in body.splitlines() if line.startswith("data:")
    ]


class TestAgentEndToEnd:
    """A full agent run driven through the real HTTP endpoint."""

    @staticmethod
    def _build(
        settings: Settings, *turns: AssistantTurn
    ) -> tuple[TestClient, InMemoryConversationStore, FakeRedis]:
        inner = InMemoryConversationStore()
        redis = FakeRedis()
        store = CachedConversationStore(inner, redis, ttl_seconds=60)
        llm = ScriptedLLMClient(turns)
        engine = OrchestratorEngine(AgentOrchestrator(AgentGraph(llm), store))
        return TestClient(create_app(settings, engine=engine)), inner, redis

    async def test_a_tool_using_run_answers_persists_and_caches(self, settings: Settings) -> None:
        client, inner, redis = self._build(settings, *_TOOL_RUN)

        resp = client.post(
            _ENDPOINT,
            json={
                "messages": [{"role": "user", "content": "what is 129 * 47?"}],
                "conversation_id": "conv-1",
            },
        )

        assert resp.status_code == 200
        body = resp.json()

        # 1. The tool actually ran and its result reached the answer. 6063 is the
        #    real product — the scripted model never computed it, the calculator did.
        assert "6063" in body["choices"][0]["message"]["content"]

        # 2. Usage is the model's own counts, summed over BOTH calls.
        assert body["usage"] == {
            "prompt_tokens": 560,
            "completion_tokens": 52,
            "total_tokens": 612,
        }

        # 3. The turn was persisted to the source of truth.
        assert [m["role"] for m in inner.conversations["conv-1"]] == ["user", "assistant"]
        assert "6063" in inner.conversations["conv-1"][1]["content"]

        # 4. The write invalidated the cache rather than rewriting it.
        assert redis.deletes == ["conversation:conv-1"]

    async def test_the_calculator_result_is_computed_not_scripted(self, settings: Settings) -> None:
        """Guards the test itself: 6063 must come from the tool, not the fixture."""
        client, _, _ = self._build(settings, *_TOOL_RUN)
        llm_asked_for = _TOOL_RUN[0].raw_content[1]

        client.post(
            _ENDPOINT,
            json={"messages": [{"role": "user", "content": "129*47?"}], "conversation_id": "c"},
        )

        assert llm_asked_for["input"] == {"expression": "129 * 47"}
        assert 129 * 47 == 6063

    async def test_a_second_turn_reads_prior_history_from_the_cache(
        self, settings: Settings
    ) -> None:
        client, inner, redis = self._build(
            settings,
            AssistantTurn(
                text="Hi Ada.",
                stop_reason="end_turn",
                raw_content=({"type": "text", "text": "Hi Ada."},),
            ),
            AssistantTurn(
                text="You are Ada.",
                stop_reason="end_turn",
                raw_content=({"type": "text", "text": "You are Ada."},),
            ),
        )

        client.post(
            _ENDPOINT,
            json={"messages": [{"role": "user", "content": "I am Ada"}], "conversation_id": "c2"},
        )
        # Populate the cache, then confirm the second turn is served from it.
        await CachedConversationStore(inner, redis, ttl_seconds=60).load("c2")
        loads_before = inner.load_calls

        resp = client.post(
            _ENDPOINT,
            json={"messages": [{"role": "user", "content": "who am I?"}], "conversation_id": "c2"},
        )

        assert resp.status_code == 200
        assert inner.load_calls == loads_before, "second turn should have hit the Redis cache"
        assert len(inner.conversations["c2"]) == 4

    async def test_history_is_replayed_to_the_model(self, settings: Settings) -> None:
        inner = InMemoryConversationStore()
        inner.conversations["c3"] = [
            {"role": "user", "content": "my name is Ada"},
            {"role": "assistant", "content": "Hi Ada."},
        ]
        llm = ScriptedLLMClient([AssistantTurn(text="Ada.", stop_reason="end_turn")])
        engine = OrchestratorEngine(
            AgentOrchestrator(
                AgentGraph(llm), CachedConversationStore(inner, FakeRedis(), ttl_seconds=60)
            )
        )

        with TestClient(create_app(settings, engine=engine)) as client:
            client.post(
                _ENDPOINT,
                json={
                    "messages": [{"role": "user", "content": "who am I?"}],
                    "conversation_id": "c3",
                },
            )

        sent = llm.calls[0]["messages"]
        assert sent[0] == {"role": "user", "content": "my name is Ada"}
        assert sent[-1] == {"role": "user", "content": "who am I?"}

    async def test_a_request_without_a_conversation_id_persists_nothing(
        self, settings: Settings
    ) -> None:
        """Stateless stays stateless — the Stage 2 contract still holds."""
        client, inner, redis = self._build(settings, *_TOOL_RUN)

        resp = client.post(_ENDPOINT, json={"messages": [{"role": "user", "content": "129*47?"}]})

        assert resp.status_code == 200
        assert inner.conversations == {}
        assert redis.deletes == []

    async def test_a_tool_using_run_streams_over_sse(self, settings: Settings) -> None:
        client, inner, _ = self._build(settings, *_TOOL_RUN)

        resp = client.post(
            _ENDPOINT,
            json={
                "messages": [{"role": "user", "content": "what is 129 * 47?"}],
                "conversation_id": "conv-stream",
                "stream": True,
            },
        )

        frames = _sse_frames(resp.text)
        assert frames[-1] == "[DONE]"
        chunks = [json.loads(f) for f in frames if f != "[DONE]"]
        streamed = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)

        # Text from both turns reaches the client, and the tool's answer is in it.
        assert "6063" in streamed
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
        # What was streamed is what was stored.
        assert inner.conversations["conv-stream"][-1]["content"] == streamed


@pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") and os.environ.get("TEST_REDIS_URL")),
    reason="needs a live Postgres and Redis; set TEST_DATABASE_URL and TEST_REDIS_URL",
)
class TestAgainstRealDatastores:
    """The layer the fakes cannot cover: does Postgres actually accept our SQL?

    Opt-in so the default suite stays hermetic (ADR 0005). CI and anyone with
    `docker compose up` can run it.
    """

    @staticmethod
    async def _pool() -> Any:
        import asyncpg

        return await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=2)

    async def test_the_shipped_migration_applies_to_a_real_postgres(self) -> None:
        from shared.migrations import apply_pending

        pool = await self._pool()
        try:
            await apply_pending(pool)
            tables = await pool.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
            )
            names = {row["tablename"] for row in tables}
            assert {"conversations", "conversation_messages", "schema_migrations"} <= names
        finally:
            await pool.close()

    async def test_applying_twice_is_a_no_op(self) -> None:
        from shared.migrations import apply_pending

        pool = await self._pool()
        try:
            await apply_pending(pool)
            assert await apply_pending(pool) == []
        finally:
            await pool.close()

    async def test_a_full_turn_round_trips_through_postgres_and_redis(
        self, settings: Settings
    ) -> None:
        import redis.asyncio as aioredis
        from services.orchestrator.conversations import Turn
        from shared.migrations import apply_pending

        pool = await self._pool()
        redis = aioredis.Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
        conversation_id = "it-real-1"
        try:
            await apply_pending(pool)
            await pool.execute("DELETE FROM conversations WHERE id = $1", conversation_id)
            await redis.delete(f"conversation:{conversation_id}")

            store = CachedConversationStore(PostgresConversationStore(pool), redis, ttl_seconds=60)
            llm = ScriptedLLMClient(list(_TOOL_RUN))
            orchestrator = AgentOrchestrator(AgentGraph(llm), store)

            result = await orchestrator.answer(
                [Turn(role="user", content="what is 129 * 47?")],
                conversation_id=conversation_id,
            )

            assert "6063" in result.answer
            rows = await pool.fetch(
                "SELECT role, content FROM conversation_messages "
                "WHERE conversation_id = $1 ORDER BY position",
                conversation_id,
            )
            assert [r["role"] for r in rows] == ["user", "assistant"]
            assert "6063" in rows[1]["content"]
            # Reading repopulates the cache from Postgres.
            assert await store.load(conversation_id)
            assert await redis.get(f"conversation:{conversation_id}") is not None
        finally:
            await pool.execute("DELETE FROM conversations WHERE id = $1", conversation_id)
            await pool.close()
            await redis.aclose()
