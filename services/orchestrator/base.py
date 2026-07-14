"""Interface stub: orchestration contract.

**Planned, not yet implemented — Stage 3+ (agent & workflow orchestration).**

Defines how multi-step workflows (graphs of agents / tools / retrieval steps)
are coordinated. Concrete orchestration (LangGraph state machines, routing,
fan-out/fan-in) is built from Stage 3 onward. See ``README.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping


class Orchestrator(ABC):
    """Coordinates a multi-step workflow and returns its final state."""

    @abstractmethod
    async def run(self, request: Mapping[str, object]) -> Mapping[str, object]:
        """Execute the workflow for ``request`` and return the result state.

        Raises:
            NotImplementedError: Always, until implemented from Stage 3 onward.
        """
        raise NotImplementedError
