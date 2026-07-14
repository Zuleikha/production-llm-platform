"""Interface stub: evaluation contract.

**Planned, not yet implemented — Stage 6 (MLOps / evaluation).**

Defines how model/agent/RAG quality is scored against a dataset. Concrete
evaluators (LLM-as-judge, retrieval metrics, regression gates) arrive in
Stage 6. See ``README.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence


class Evaluator(ABC):
    """Scores system outputs against a dataset of cases."""

    @abstractmethod
    async def evaluate(self, dataset: Sequence[Mapping[str, object]]) -> Mapping[str, float]:
        """Run evaluation over ``dataset`` and return named metric scores.

        Raises:
            NotImplementedError: Always, until implemented in Stage 6.
        """
        raise NotImplementedError
