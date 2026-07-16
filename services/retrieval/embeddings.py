"""The embeddings seam: one protocol, a real Voyage client, and an offline double.

This is Stage 3's :mod:`services.orchestrator.llm` pattern applied to a second
paid vendor, and it is deliberately the *same* pattern rather than a new one.

**Anthropic has no embeddings API.** Voyage is the documented pairing for Claude
on RAG workloads, so the platform now talks to two vendors. See ADR 0011.

**The test profile cannot reach Voyage.** Not "does not" — *cannot*.
:class:`VoyageEmbeddingsClient` refuses to construct when the ``test`` profile is
active, before any key is read, so there is no ordering of imports, fixtures or
monkeypatches that turns a unit test into a paid API call. The guard keys on the
*profile*, never on the key's absence: a developer with ``VOYAGE_API_KEY``
exported gets exactly the hermetic suite CI gets. See ADR 0009 for the original
argument and ADR 0011 for why it is repeated here rather than generalised.

**Documents and queries are embedded differently.** Voyage takes an
``input_type`` and produces asymmetric vectors: a passage is embedded as
something to be *found*, a question as something to *find with*. Getting this
backwards does not error — it silently degrades recall — which is why the
protocol has two methods rather than one method with a flag defaulted somewhere.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

import voyageai
from shared.logging import get_logger
from shared.observability import traced

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shared.config import Settings

_logger = get_logger("retrieval.embeddings")

# Voyage rejects a request carrying more than this many texts. Ingestion of a
# real corpus exceeds it easily, so batching is the client's job, not callers'.
_VOYAGE_MAX_BATCH: Final[int] = 128

Vector = list[float]


@runtime_checkable
class EmbeddingsClient(Protocol):
    """Turns text into vectors, asymmetrically."""

    @property
    def dimensions(self) -> int:
        """Length of every vector this client returns.

        Load-bearing: it is the Qdrant collection's vector size, which is fixed
        at creation time (ADR 0012).
        """
        ...

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        """Embed passages for storage. Returns one vector per input, in order."""
        ...

    async def embed_query(self, text: str) -> Vector:
        """Embed one question for searching."""
        ...


class VoyageEmbeddingsClient:
    """The real client. Calls the Voyage AI API and costs money.

    Cannot be constructed under the ``test`` profile — see the module docstring
    and ADR 0011.
    """

    def __init__(self, settings: Settings) -> None:
        if settings.is_test:
            raise RuntimeError(
                "VoyageEmbeddingsClient must never be constructed under the 'test' "
                "profile: the suite is hermetic by construction and makes no paid "
                "API calls. Use build_embeddings_client(settings), which returns a "
                "deterministic offline double here."
            )
        if not settings.voyage_api_key:
            raise ValueError("VOYAGE_API_KEY is not set; it is required to call the Voyage API")
        self._model = settings.voyage_model
        self._dimensions = settings.voyage_embedding_dimensions
        self._client = voyageai.AsyncClient(
            api_key=settings.voyage_api_key,
            timeout=settings.voyage_timeout_seconds,
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def _embed(self, texts: Sequence[str], *, input_type: str) -> list[Vector]:
        """Embed ``texts`` in batches Voyage will accept, preserving order."""
        vectors: list[Vector] = []
        total_tokens = 0
        for start in range(0, len(texts), _VOYAGE_MAX_BATCH):
            batch = list(texts[start : start + _VOYAGE_MAX_BATCH])
            result = await self._client.embed(
                batch,
                model=self._model,
                input_type=input_type,
                output_dimension=self._dimensions,
            )
            vectors.extend(self._as_floats(result.embeddings))
            total_tokens += result.total_tokens

        _logger.info(
            "embeddings.embedded",
            extra={
                "model": self._model,
                "input_type": input_type,
                "texts": len(texts),
                "total_tokens": total_tokens,
            },
        )
        return vectors

    @staticmethod
    def _as_floats(embeddings: object) -> list[Vector]:
        """Narrow Voyage's ``list[list[float]] | list[list[int]]`` to floats.

        The int variant only appears for quantised ``output_dtype`` values we do
        not request, but the union is what the SDK declares, so narrow it rather
        than cast — a shape we did not expect should fail here, loudly, not as a
        Qdrant rejection three frames later.
        """
        if not isinstance(embeddings, list):  # pragma: no cover - SDK always returns a list
            raise TypeError(f"Voyage returned {type(embeddings).__name__}, expected a list")
        vectors: list[Vector] = []
        for row in embeddings:
            if not isinstance(row, list):  # pragma: no cover - SDK always returns lists
                raise TypeError(
                    f"Voyage returned a {type(row).__name__} embedding, expected a list"
                )
            vectors.append([float(value) for value in row])
        return vectors

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        """Embed passages for storage.

        Not decorated with ``@traced``: the call is already logged above with
        the token count, which is the number worth tracing.
        """
        if not texts:
            return []
        return await self._embed(texts, input_type="document")

    async def embed_query(self, text: str) -> Vector:
        """Embed one question for searching."""
        vectors = await self._embed([text], input_type="query")
        return vectors[0]


class HashingEmbeddingsClient:
    """A deterministic, offline ``EmbeddingsClient``. What the ``test`` profile runs.

    Real code, not a stub — the same bargain :class:`ScriptedLLMClient` strikes
    (ADR 0009). It implements the protocol honestly and produces vectors with
    **genuine cosine similarity**: text is hashed into a bag-of-words vector and
    L2-normalised, so lexical overlap really does drive the score. That is what
    lets the integration test assert that querying "how do I roll back a
    deployment" returns the rollback document *because retrieval worked*, rather
    than because a mock was told to return it.

    What it does not do is understand meaning. It scores no better than keyword
    overlap, and it makes no network call. Both are the point.

    Asymmetry is modelled as a no-op: documents and queries embed identically
    here. Voyage's asymmetry is a property of a trained model, and pretending to
    reproduce it with a hash would be a fake that lies about the real one.
    """

    _TOKEN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")

    def __init__(self, dimensions: int = 1024) -> None:
        if dimensions <= 0:
            raise ValueError(f"dimensions must be positive, got {dimensions}")
        self._dimensions = dimensions
        self.embed_calls: list[tuple[str, int]] = []

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _vector(self, text: str) -> Vector:
        vector = [0.0] * self._dimensions
        for token in self._TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            # A sign bit from an independent slice of the digest, so hash
            # collisions cancel as often as they compound instead of always
            # inflating a bucket.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            # No tokens at all (empty or punctuation-only text). A zero vector
            # has no direction; Qdrant's cosine distance would reject it.
            # Return a unit vector on a fixed axis: consistent, and orthogonal
            # to almost everything, which is the honest answer for "no content".
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        self.embed_calls.append(("document", len(texts)))
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> Vector:
        self.embed_calls.append(("query", 1))
        return self._vector(text)


@traced
def build_embeddings_client(settings: Settings) -> EmbeddingsClient:
    """Return the embeddings client the active profile is allowed to use.

    The enforcement point named in ADR 0011, mirroring ``build_llm_client``:
    under ``test`` it returns the offline double and the real client is never
    constructed. Belt and braces with :class:`VoyageEmbeddingsClient`'s own
    refusal — code that reaches past this factory still cannot dial out.
    """
    if settings.is_test:
        _logger.info(
            "embeddings.client_selected",
            extra={"client": "hashing", "reason": "test profile"},
        )
        return HashingEmbeddingsClient(dimensions=settings.voyage_embedding_dimensions)
    _logger.info(
        "embeddings.client_selected",
        extra={"client": "voyage", "model": settings.voyage_model},
    )
    return VoyageEmbeddingsClient(settings)
