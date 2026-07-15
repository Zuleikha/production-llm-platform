"""The agent contract, and the tool-using agent that implements it.

Stage 1 shipped only the ABC below; Stage 3 fills it in with :class:`ToolAgent`,
which runs the LangGraph loop in ``services.orchestrator.graph``.

An ``Agent`` is deliberately the *narrow* surface — "given a task, return an
answer". Anything about conversations, persistence or transports belongs to the
orchestrator above it, which is what lets the same agent be driven by a chat
request today and by an evaluation harness in Stage 6.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from shared.observability import traced

if TYPE_CHECKING:
    from services.orchestrator.graph import AgentGraph


class Agent(ABC):
    """A single autonomous agent that acts on a task and returns a result."""

    @abstractmethod
    async def run(self, task: str) -> str:
        """Execute ``task`` and return the agent's final answer."""


class ToolAgent(Agent):
    """An agent that reasons, calls tools, observes results, and answers.

    A thin adapter over :class:`~services.orchestrator.graph.AgentGraph`: the
    loop, the step cap and the tool dispatch all live in the graph, because the
    orchestrator needs those same mechanics for streaming and would otherwise
    reimplement them here.
    """

    def __init__(self, graph: AgentGraph) -> None:
        self._graph = graph

    @traced
    async def run(self, task: str) -> str:
        """Run ``task`` to a final answer, discarding usage and intermediate state.

        Callers that need token accounting should drive the graph (or the
        orchestrator) directly — this signature returns only the answer, and
        widening it would change the contract every stage after this depends on.
        """
        state = await self._graph.run([{"role": "user", "content": task}])
        return state["answer"]
