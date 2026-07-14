"""Interface stub: agent contract.

**Planned, not yet implemented — Stage 3 (agents).**

Defines the abstract contract an agent must satisfy. No behaviour is provided in
Stage 1; concrete agents (tool-use loops, LangChain/LangGraph-backed) arrive in
Stage 3. See ``services/agents/README.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Agent(ABC):
    """A single autonomous agent that acts on a task and returns a result."""

    @abstractmethod
    async def run(self, task: str) -> str:
        """Execute ``task`` and return the agent's final answer.

        Raises:
            NotImplementedError: Always, until implemented in Stage 3.
        """
        raise NotImplementedError
