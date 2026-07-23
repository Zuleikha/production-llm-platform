"""End-to-end retrieval: ingest -> embed -> store -> retrieve -> cite.

Three layers, because they prove different things and only one runs by default:

- :class:`TestCitationsReachTheClient` is **hermetic** and always runs. It drives
  the real HTTP endpoint with a scripted model that calls ``document_search``,
  and proves the citation path — tool -> graph state -> orchestrator -> engine ->
  wire — on both the whole and streamed responses. What it does not prove is that
  Qdrant accepts our points or ranks anything.

- :class:`TestAgainstRealQdrant` closes exactly that gap: the shipped corpus is
  chunked, embedded and written to a **live Qdrant**, then queried back through
  the real ``VectorRetriever`` and the real tool. Skipped unless
  ``TEST_QDRANT_URL`` is set, so the default suite stays hermetic (ADR 0005).
  The embeddings client is still the offline double — this layer is about
  Qdrant, not about Voyage.

- :class:`TestLiveProviderContract` is the one that costs money. It makes one
  real call each to Anthropic and Voyage and asserts the response shapes the code
  assumes. Skipped unless ``RUN_LIVE_CONTRACT_TESTS=1`` **and** the keys are set.
  See ADR 0015 for why the hermetic suite cannot cover this.

No layer here ever calls a paid API without that explicit opt-in: the `test`
profile cannot construct either real client (ADR 0009, ADR 0011), and the live
class builds its clients from a non-test profile deliberately.
"""

from __future__ import annotations

import json
import math
import os
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from services.agents.tools import ToolRegistry
from services.api.app import create_app
from services.api.completions import OrchestratorEngine
from services.orchestrator.base import AgentOrchestrator
from services.orchestrator.conversations import CachedConversationStore
from services.orchestrator.graph import AgentGraph
from services.orchestrator.llm import AssistantTurn, ScriptedLLMClient, TokenUsage
from services.retrieval.base import RetrievedDocument
from services.retrieval.embeddings import HashingEmbeddingsClient
from services.retrieval.tool import DocumentSearch

from tests.fakes import AUTH_HEADERS, FakeRedis, InMemoryConversationStore, StubRetriever

if TYPE_CHECKING:
    from shared.config import Settings

_ENDPOINT = "/v1/chat/completions"
_DIMENSIONS = 256

_ROLLBACK_DOC = RetrievedDocument(
    id="deployments.md:1",
    text="To roll back, shift traffic to the previous version.",
    score=0.87,
    document_id="deployments.md",
    source="deployments.md",
    position=1,
)

# The model searches the corpus, reads the excerpts, then answers from them.
_SEARCH_RUN = (
    AssistantTurn(
        usage=TokenUsage(input_tokens=300, output_tokens=30),
        stop_reason="tool_use",
        raw_content=(
            {"type": "text", "text": "Let me check the docs. "},
            {
                "type": "tool_use",
                "id": "toolu_search",
                "name": "document_search",
                "input": {"query": "how do I roll back a deployment"},
            },
        ),
    ),
    AssistantTurn(
        text="Shift traffic back to the previous version.",
        usage=TokenUsage(input_tokens=420, output_tokens=14),
        stop_reason="end_turn",
        raw_content=({"type": "text", "text": "Shift traffic back to the previous version."},),
    ),
)


def _sse_frames(body: str) -> list[str]:
    return [
        line.removeprefix("data:").strip() for line in body.splitlines() if line.startswith("data:")
    ]


def _client_with(settings: Settings, tools: ToolRegistry, *turns: AssistantTurn) -> TestClient:
    store = CachedConversationStore(InMemoryConversationStore(), FakeRedis(), ttl_seconds=60)
    graph = AgentGraph(ScriptedLLMClient(turns), tools=tools)
    engine = OrchestratorEngine(AgentOrchestrator(graph, store))
    return TestClient(create_app(settings, engine=engine), headers=AUTH_HEADERS)


class TestCitationsReachTheClient:
    """The wire path for citations (ADR 0013)."""

    @staticmethod
    def _tools() -> ToolRegistry:
        return ToolRegistry.default().with_tools(DocumentSearch(StubRetriever([_ROLLBACK_DOC])))

    async def test_a_grounded_answer_carries_its_citations(self, settings: Settings) -> None:
        client = _client_with(settings, self._tools(), *_SEARCH_RUN)

        resp = client.post(
            _ENDPOINT, json={"messages": [{"role": "user", "content": "how do I roll back?"}]}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["citations"] == [
            {
                "id": "deployments.md:1",
                "document_id": "deployments.md",
                "source": "deployments.md",
                "score": 0.87,
                "text": "To roll back, shift traffic to the previous version.",
            }
        ]
        # Citations are a NEW top-level field — the Stage 2/3 shape is untouched.
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert body["usage"] == {
            "prompt_tokens": 720,
            "completion_tokens": 44,
            "total_tokens": 764,
        }

    async def test_an_ungrounded_answer_reports_no_citations(self, settings: Settings) -> None:
        """Empty, not absent: "nothing grounded this" is a fact worth stating."""
        client = _client_with(
            settings,
            self._tools(),
            AssistantTurn(
                text="4.",
                stop_reason="end_turn",
                raw_content=({"type": "text", "text": "4."},),
            ),
        )

        resp = client.post(_ENDPOINT, json={"messages": [{"role": "user", "content": "2+2?"}]})

        assert resp.status_code == 200
        assert resp.json()["citations"] == []

    async def test_citations_arrive_on_the_final_sse_frame_only(self, settings: Settings) -> None:
        client = _client_with(settings, self._tools(), *_SEARCH_RUN)

        resp = client.post(
            _ENDPOINT,
            json={
                "messages": [{"role": "user", "content": "how do I roll back?"}],
                "stream": True,
            },
        )

        frames = _sse_frames(resp.text)
        assert frames[-1] == "[DONE]"
        chunks = [json.loads(f) for f in frames if f != "[DONE]"]

        final = chunks[-1]
        assert final["choices"][0]["finish_reason"] == "stop"
        assert [c["id"] for c in final["citations"]] == ["deployments.md:1"]
        # Every text frame omits the field entirely, rather than repeating it.
        assert all("citations" not in chunk for chunk in chunks[:-1])

    async def test_the_streamed_and_whole_paths_report_the_same_citations(
        self, settings: Settings
    ) -> None:
        """Streaming is a transport choice; it must not change the provenance."""
        whole = _client_with(settings, self._tools(), *_SEARCH_RUN).post(
            _ENDPOINT, json={"messages": [{"role": "user", "content": "roll back?"}]}
        )
        streamed = _client_with(settings, self._tools(), *_SEARCH_RUN).post(
            _ENDPOINT,
            json={"messages": [{"role": "user", "content": "roll back?"}], "stream": True},
        )

        final_frame = json.loads(_sse_frames(streamed.text)[-2])
        assert whole.json()["citations"] == final_frame["citations"]

    async def test_the_same_chunk_found_twice_is_cited_once(self, settings: Settings) -> None:
        """An agent may search repeatedly; the client should see each source once."""
        searches = (
            _SEARCH_RUN[0],
            AssistantTurn(
                usage=TokenUsage(input_tokens=10, output_tokens=5),
                stop_reason="tool_use",
                raw_content=(
                    {
                        "type": "tool_use",
                        "id": "toolu_search_2",
                        "name": "document_search",
                        "input": {"query": "rollback window"},
                    },
                ),
            ),
            _SEARCH_RUN[1],
        )
        client = _client_with(settings, self._tools(), *searches)

        resp = client.post(
            _ENDPOINT, json={"messages": [{"role": "user", "content": "roll back?"}]}
        )

        assert [c["id"] for c in resp.json()["citations"]] == ["deployments.md:1"]

    async def test_a_failing_retrieval_cites_nothing_but_still_answers(
        self, settings: Settings
    ) -> None:
        """An error result is not grounding. Citing it would be a lie."""

        class _BrokenRetriever:
            async def retrieve(self, query: str, *, top_k: int | None = None) -> Any:
                raise RuntimeError("qdrant is down")

        tools = ToolRegistry.default().with_tools(DocumentSearch(_BrokenRetriever()))
        client = _client_with(settings, tools, *_SEARCH_RUN)

        resp = client.post(
            _ENDPOINT, json={"messages": [{"role": "user", "content": "roll back?"}]}
        )

        assert resp.status_code == 200
        assert resp.json()["citations"] == []


@pytest.mark.skipif(
    not os.environ.get("TEST_QDRANT_URL"),
    reason="needs a live Qdrant; set TEST_QDRANT_URL",
)
class TestAgainstRealQdrant:
    """The layer the fakes cannot cover: does Qdrant accept our points and rank them?

    Opt-in so the default suite stays hermetic (ADR 0005). CI and anyone with
    `docker compose up` can run it:

        TEST_QDRANT_URL=http://localhost:6333 uv run pytest -k RealQdrant
    """

    _COLLECTION = "test_stage04_documents"

    @staticmethod
    def _qdrant() -> Any:
        from qdrant_client import AsyncQdrantClient

        return AsyncQdrantClient(url=os.environ["TEST_QDRANT_URL"], timeout=30)

    async def _ingested_store(self, client: Any) -> Any:
        """Ingest the shipped corpus into a scratch collection."""
        from pathlib import Path

        from services.retrieval.ingest import ingest_corpus, load_corpus
        from services.retrieval.store import QdrantVectorStore

        store = QdrantVectorStore(client, collection=self._COLLECTION, dimensions=_DIMENSIONS)
        corpus = Path(__file__).resolve().parents[2] / "data" / "corpus"
        await ingest_corpus(
            load_corpus(corpus),
            embeddings=HashingEmbeddingsClient(dimensions=_DIMENSIONS),
            store=store,
            chunk_size=128,
            chunk_overlap=16,
        )
        return store

    async def test_the_full_slice_ingests_stores_retrieves_and_cites(
        self, settings: Settings
    ) -> None:
        """ingest -> embed -> store -> retrieve -> cite, against real Qdrant.

        The scripted model only *asks* for a search; the excerpt it answers from
        is whatever real cosine search over real stored vectors returned.
        """
        from services.retrieval.retriever import VectorRetriever

        client = self._qdrant()
        try:
            store = await self._ingested_store(client)
            retriever = VectorRetriever(
                HashingEmbeddingsClient(dimensions=_DIMENSIONS), store, top_k=3, min_score=0.05
            )
            tools = ToolRegistry.default().with_tools(DocumentSearch(retriever))

            http = _client_with(settings, tools, *_SEARCH_RUN)
            resp = http.post(
                _ENDPOINT,
                json={
                    "messages": [{"role": "user", "content": "how do I roll back a deployment?"}]
                },
            )

            assert resp.status_code == 200
            citations = resp.json()["citations"]
            assert citations, "real Qdrant search returned nothing for a corpus question"
            # The rollback question must retrieve the deployments document — this
            # is real ranking over real vectors, not a fixture.
            assert citations[0]["document_id"] == "deployments.md"
            assert citations[0]["score"] > 0
            assert "roll back" in citations[0]["text"].lower()
        finally:
            await client.delete_collection(self._COLLECTION)
            await client.close()

    async def test_re_ingesting_updates_points_in_place_rather_than_duplicating(self) -> None:
        """Deterministic chunk ids -> deterministic point UUIDs -> idempotent."""
        client = self._qdrant()
        try:
            await self._ingested_store(client)
            first = (await client.count(self._COLLECTION, exact=True)).count
            await self._ingested_store(client)
            second = (await client.count(self._COLLECTION, exact=True)).count
            assert first == second > 0
        finally:
            await client.delete_collection(self._COLLECTION)
            await client.close()

    async def test_qdrant_accepts_our_point_ids(self) -> None:
        """The trap: PointStruct.id is typed `int | str | UUID`, but the SERVER
        rejects an arbitrary string. This is the test that would have caught it."""
        from services.retrieval.store import point_id_for

        client = self._qdrant()
        try:
            store = await self._ingested_store(client)
            retrieved = await client.retrieve(
                self._COLLECTION, ids=[point_id_for("deployments.md:0")], with_payload=True
            )
            assert len(retrieved) == 1
            assert retrieved[0].payload is not None
            assert retrieved[0].payload["chunk_id"] == "deployments.md:0"
            assert retrieved[0].payload["document_id"] == "deployments.md"
            assert store is not None
        finally:
            await client.delete_collection(self._COLLECTION)
            await client.close()

    async def test_the_datastore_probe_works_against_a_real_qdrant(self) -> None:
        """Stage 4 swapped the /readyz httpx probe for get_collections."""
        from shared.datastores import QdrantDatastore

        store = QdrantDatastore(os.environ["TEST_QDRANT_URL"], max_connections=4, timeout=5.0)
        try:
            await store.connect()
            await store.ping()
            assert store.client is not None
        finally:
            await store.close()


@pytest.mark.skipif(
    not (
        os.environ.get("RUN_LIVE_CONTRACT_TESTS") == "1"
        and os.environ.get("ANTHROPIC_API_KEY")
        and os.environ.get("VOYAGE_API_KEY")
    ),
    reason=(
        "makes real, billable API calls; set RUN_LIVE_CONTRACT_TESTS=1 plus "
        "ANTHROPIC_API_KEY and VOYAGE_API_KEY to run"
    ),
)
class TestLiveProviderContract:
    """One real call to each provider, asserting the shapes the code assumes.

    THIS COSTS MONEY. Double opt-in (an explicit flag *and* the keys) because a
    key alone is not consent — a developer has one exported all the time.

    Closes the gap ADR 0009 named and Stage 3 left open: the hermetic doubles
    encode our *beliefs* about these APIs, so if a belief is wrong the fake and
    the code are wrong together and every test still passes. Only a real call can
    catch a provider-side rename. See ADR 0015.

        RUN_LIVE_CONTRACT_TESTS=1 uv run pytest -k LiveProviderContract -s
    """

    @staticmethod
    def _live_settings() -> Any:
        """Settings on a NON-test profile — the only way to build a real client.

        The `test` profile refuses by construction (ADR 0009, ADR 0011), which is
        exactly the guard this class has to step around on purpose.
        """
        from shared.config import Settings

        return Settings(
            _env_file=None,
            environment="dev",
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            voyage_api_key=os.environ["VOYAGE_API_KEY"],
        )

    async def test_the_anthropic_chat_api_still_has_the_shape_we_assume(self) -> None:
        """Pins what `AnthropicClient.stream` reads off a real response."""
        from services.orchestrator.llm import AnthropicClient, TextDelta, TurnCompleted

        client = AnthropicClient(self._live_settings())
        try:
            events = [
                event
                async for event in client.stream(
                    system="You are terse. Answer with a single word.",
                    messages=[{"role": "user", "content": "What is the capital of France?"}],
                    tools=[],
                    max_tokens=16,
                )
            ]
        finally:
            await client.aclose()

        completed = [e for e in events if isinstance(e, TurnCompleted)]
        assert len(completed) == 1, "stream must end with exactly one TurnCompleted"
        turn = completed[0].turn

        # The fields the agent loop and the usage accounting actually depend on.
        assert any(isinstance(e, TextDelta) for e in events), "no text_delta events arrived"
        assert turn.text.strip(), "completed turn carried no text"
        assert turn.stop_reason == "end_turn"
        assert turn.usage.input_tokens > 0, "usage.input_tokens missing or renamed"
        assert turn.usage.output_tokens > 0, "usage.output_tokens missing or renamed"
        assert turn.raw_content, "raw_content is what gets replayed to the API next turn"
        assert turn.raw_content[0]["type"] == "text"
        print(
            f"\n[live anthropic] text={turn.text.strip()!r} "
            f"stop_reason={turn.stop_reason} "
            f"usage=in:{turn.usage.input_tokens}/out:{turn.usage.output_tokens}"
        )

    async def test_the_voyage_embeddings_api_still_has_the_shape_we_assume(self) -> None:
        """Pins what `VoyageEmbeddingsClient` reads off a real response.

        Including the one that cannot be caught any other way: that the model
        really does return `voyage_embedding_dimensions` floats. Qdrant's
        collection vector size is fixed at creation from this number, so a wrong
        belief here is a write failure in production (ADR 0012).
        """
        from services.retrieval.embeddings import VoyageEmbeddingsClient

        settings = self._live_settings()
        client = VoyageEmbeddingsClient(settings)

        vectors = await client.embed_documents(
            ["To roll back, shift traffic to the previous version.", "Severity is customer impact."]
        )
        query = await client.embed_query("how do I roll back a deployment?")

        assert len(vectors) == 2, "one vector per input, in order"
        assert all(len(v) == settings.voyage_embedding_dimensions for v in vectors), (
            f"model {settings.voyage_model} did not return "
            f"{settings.voyage_embedding_dimensions}-dim vectors — the Qdrant "
            "collection would be created with the wrong vector size"
        )
        assert len(query) == settings.voyage_embedding_dimensions
        assert all(isinstance(value, float) for value in query)

        # Asymmetric embedding is the reason there are two methods, not one.
        # The rollback query must sit closer to the rollback passage than to the
        # severity one — if not, input_type is not doing what we think.
        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=True))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            return dot / (na * nb)

        related, unrelated = cosine(query, vectors[0]), cosine(query, vectors[1])
        assert related > unrelated, "query/document embeddings do not rank as expected"
        print(
            f"\n[live voyage] model={settings.voyage_model} "
            f"dims={len(query)} cos(related)={related:.4f} cos(unrelated)={unrelated:.4f}"
        )
