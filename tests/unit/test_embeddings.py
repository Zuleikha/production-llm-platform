"""Tests for the embeddings seam — and, mostly, for the guard around it.

The interesting tests here are :class:`TestTestProfileCannotCallVoyage`. They pin
the *mechanism* that makes the suite hermetic against a second paid vendor, in
the same way ``test_llm.py`` pins it for Anthropic (ADR 0009, ADR 0011). If
someone replaces the guard with a convention — an autouse fixture, a
monkeypatch, "we mock it in tests" — these fail.
"""

from __future__ import annotations

import math

import pytest
from services.retrieval.embeddings import (
    EmbeddingsClient,
    HashingEmbeddingsClient,
    VoyageEmbeddingsClient,
    build_embeddings_client,
)
from shared.config import Settings, get_settings


def _prod_settings(**overrides: object) -> Settings:
    """Settings for a non-test profile, with every prod requirement satisfied."""
    defaults: dict[str, object] = {
        "_env_file": None,
        "environment": "dev",
        "voyage_api_key": "pa-not-a-real-key",
    }
    return Settings(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestTestProfileCannotCallVoyage:
    """The hermetic guard. See ADR 0011."""

    def test_the_real_client_refuses_to_construct_under_the_test_profile(self) -> None:
        settings = Settings(_env_file=None, environment="test")
        with pytest.raises(RuntimeError, match="never be constructed under the 'test' profile"):
            VoyageEmbeddingsClient(settings)

    def test_it_refuses_even_when_a_valid_looking_key_is_supplied(self) -> None:
        """The guard keys on the PROFILE, not on the key's absence.

        This is the whole design: a developer with VOYAGE_API_KEY exported must
        get exactly the suite CI gets. A guard on "no key set" would behave
        differently — and bill — precisely where the code is being written.
        """
        settings = Settings(
            _env_file=None, environment="test", voyage_api_key="pa-looks-real-enough"
        )
        with pytest.raises(RuntimeError, match="never be constructed under the 'test' profile"):
            VoyageEmbeddingsClient(settings)

    def test_a_real_key_in_the_os_environment_does_not_change_that(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case that distinguishes hermetic from merely-unconfigured."""
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("VOYAGE_API_KEY", "pa-a-real-looking-key-from-the-shell")
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert settings.voyage_api_key == "pa-a-real-looking-key-from-the-shell"
            with pytest.raises(RuntimeError, match="never be constructed"):
                VoyageEmbeddingsClient(settings)
            assert isinstance(build_embeddings_client(settings), HashingEmbeddingsClient)
        finally:
            get_settings.cache_clear()

    def test_the_factory_returns_the_double_under_the_test_profile(self) -> None:
        settings = Settings(_env_file=None, environment="test")
        assert isinstance(build_embeddings_client(settings), HashingEmbeddingsClient)

    def test_the_running_suite_is_on_the_double(self, settings: Settings) -> None:
        """Not a tautology: this asserts the *active* profile, as loaded."""
        assert settings.is_test
        assert isinstance(build_embeddings_client(settings), HashingEmbeddingsClient)

    def test_the_factory_returns_the_real_client_outside_the_test_profile(self) -> None:
        """Constructing it dials nothing — no request is made until embed()."""
        client = build_embeddings_client(_prod_settings())
        assert isinstance(client, VoyageEmbeddingsClient)

    def test_the_real_client_requires_a_key_outside_the_test_profile(self) -> None:
        with pytest.raises(ValueError, match="VOYAGE_API_KEY is not set"):
            VoyageEmbeddingsClient(_prod_settings(voyage_api_key=None))


class TestHashingEmbeddingsClient:
    """The double is real code, not a stub — so its behaviour is worth pinning."""

    async def test_it_satisfies_the_protocol(self) -> None:
        assert isinstance(HashingEmbeddingsClient(), EmbeddingsClient)

    async def test_vectors_have_the_configured_dimensionality(self) -> None:
        client = HashingEmbeddingsClient(dimensions=64)
        assert client.dimensions == 64
        assert len(await client.embed_query("hello")) == 64
        assert [len(v) for v in await client.embed_documents(["a", "b"])] == [64, 64]

    async def test_it_is_deterministic(self) -> None:
        """A double that varied would make every retrieval assertion flaky."""
        a = await HashingEmbeddingsClient(dimensions=64).embed_query("rollback a deployment")
        b = await HashingEmbeddingsClient(dimensions=64).embed_query("rollback a deployment")
        assert a == b

    async def test_vectors_are_unit_length(self) -> None:
        """Qdrant's cosine distance is meaningless for a non-normalised vector."""
        vector = await HashingEmbeddingsClient(dimensions=128).embed_query("some text here")
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)

    async def test_similar_text_scores_higher_than_unrelated_text(self) -> None:
        """The property the integration test leans on.

        The double has to produce *genuine* cosine similarity, or a passing
        retrieval test would prove only that a mock returned what it was told to.
        """
        client = HashingEmbeddingsClient(dimensions=512)

        def cosine(a: list[float], b: list[float]) -> float:
            return sum(x * y for x, y in zip(a, b, strict=True))

        query = await client.embed_query("how do I roll back a deployment")
        related = await client.embed_query("rolling back a deployment is done by shifting traffic")
        unrelated = await client.embed_query("cardinality of metric labels in prometheus")

        assert cosine(query, related) > cosine(query, unrelated)

    async def test_empty_text_gets_a_usable_unit_vector(self) -> None:
        """A zero vector has no direction and Qdrant rejects it for cosine."""
        vector = await HashingEmbeddingsClient(dimensions=32).embed_query("!!! ...")
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)

    async def test_embedding_no_documents_is_not_an_error(self) -> None:
        assert await HashingEmbeddingsClient().embed_documents([]) == []

    async def test_it_records_what_it_was_asked(self) -> None:
        client = HashingEmbeddingsClient(dimensions=32)
        await client.embed_documents(["a", "b", "c"])
        await client.embed_query("q")
        assert client.embed_calls == [("document", 3), ("query", 1)]

    def test_it_rejects_a_nonsense_dimensionality(self) -> None:
        with pytest.raises(ValueError, match="dimensions must be positive"):
            HashingEmbeddingsClient(dimensions=0)
