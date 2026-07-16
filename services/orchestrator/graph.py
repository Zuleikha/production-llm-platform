"""The agent loop, as a LangGraph state machine.

The graph is two nodes and one decision:

    START -> reason -> (tool_calls? -> act -> reason) -> END

``reason`` calls the model; ``act`` executes every tool the model asked for and
appends the results; the conditional edge sends control back to ``reason`` so it
can observe them. That is the task -> reason -> select tool -> execute ->
observe -> continue-or-answer loop, made explicit rather than implied by a
``while``. See ADR 0006 for why a graph at all.

Two properties are worth stating because they are easy to lose:

**One graph serves both transports.** ``reason`` pushes text into LangGraph's
custom stream as the model produces it. A streaming caller reads that stream; a
non-streaming caller invokes the same graph and reads the final state. There is
no second loop for SSE to drift away from — which is exactly the bug the Stage 2
seam existed to prevent.

**The loop is bounded.** ``agent_max_steps`` caps model calls. A model that
keeps requesting tools stops being an agent and starts being a bill.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from shared.logging import get_logger
from shared.observability import traced

from services.agents.tools import Citation, ToolError, ToolRegistry
from services.orchestrator.llm import (
    AssistantTurn,
    TextDelta,
    TokenUsage,
    TurnCompleted,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from langgraph.graph.state import CompiledStateGraph

    from services.orchestrator.llm import LLMClient

_logger = get_logger("orchestrator.graph")

SYSTEM_PROMPT = (
    "You are a helpful engineering assistant.\n"
    "\n"
    "You have tools available. Use a tool when it would make your answer more "
    "accurate than reasoning alone would — in particular, use the calculator "
    "for arithmetic rather than computing in your head. When no tool is "
    "relevant, just answer directly.\n"
    "\n"
    "Answer the user's question. Be concise and lead with the answer."
)


def _accumulate_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    """Reducer: token usage sums across every turn of a run."""
    return left + right


def _accumulate_citations(
    left: tuple[Citation, ...], right: tuple[Citation, ...]
) -> tuple[Citation, ...]:
    """Reducer: citations accumulate across every tool call of a run, deduped.

    An agent may search several times before answering, and the same chunk can
    come back from more than one query — the client should see it once. First
    occurrence wins, so the order reflects when the agent actually found each
    source. Deduping here rather than at the API boundary keeps the reported
    citations consistent between the streamed and non-streamed paths, which read
    different parts of this state.
    """
    merged = list(left)
    seen = {citation.id for citation in left}
    for citation in right:
        if citation.id not in seen:
            seen.add(citation.id)
            merged.append(citation)
    return tuple(merged)


class AgentState(TypedDict):
    """What flows through the graph.

    ``messages`` is the Anthropic wire format, not a domain type, and is
    replaced wholesale by each node rather than reduced. Tool results must be
    replayed to the API exactly as the model emitted the matching tool_use
    block, so a reducer that rebuilt or merged blocks would corrupt the turn.
    """

    messages: list[dict[str, Any]]
    answer: str
    usage: Annotated[TokenUsage, _accumulate_usage]
    steps: int
    stop_reason: str | None
    # Per-run output cap. Lives in state rather than on the graph so one
    # compiled graph can serve requests with different max_tokens — the Anthropic
    # API requires the parameter on every call and the client may set it.
    max_tokens: int
    # Sources the tools consulted, accumulated across the run (Stage 4). Carried
    # as typed data alongside the messages rather than parsed back out of them:
    # the text the model saw is fenced, untrusted document content, and rebuilding
    # provenance from it would let a document forge its own citation (ADR 0013).
    citations: Annotated[tuple[Citation, ...], _accumulate_citations]


def _as_state(value: object) -> AgentState:
    """Narrow LangGraph's loosely-typed output back to :class:`AgentState`.

    ``ainvoke``/``astream`` are typed to return the state channel but resolve to
    ``Any`` in practice, so mypy cannot see the shape. Rebuilding the TypedDict
    field by field — rather than casting — means a graph that ever returns
    something unexpected fails here, loudly, instead of surfacing as an
    ``AttributeError`` deep in the route.
    """
    if not isinstance(value, dict):  # pragma: no cover - the graph always returns its state
        raise TypeError(f"agent graph returned {type(value).__name__}, expected a state mapping")
    usage = value.get("usage")
    stop_reason = value.get("stop_reason")
    citations = value.get("citations")
    return AgentState(
        messages=list(value.get("messages", [])),
        answer=str(value.get("answer", "")),
        usage=usage if isinstance(usage, TokenUsage) else TokenUsage(),
        steps=int(value.get("steps", 0)),
        stop_reason=stop_reason if isinstance(stop_reason, str) else None,
        max_tokens=int(value.get("max_tokens", 0)),
        citations=(
            tuple(c for c in citations if isinstance(c, Citation))
            if isinstance(citations, tuple | list)
            else ()
        ),
    )


class AgentGraph:
    """A compiled agent loop, bound to one LLM client and tool registry."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        tools: ToolRegistry | None = None,
        max_steps: int = 6,
        max_tokens: int = 4096,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._llm = llm
        self._tools = tools or ToolRegistry.default()
        self._max_steps = max_steps
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt
        self._specifications = self._tools.specifications()
        self._graph = self._build()

    def _build(self) -> CompiledStateGraph[AgentState, Any, AgentState, AgentState]:
        builder: StateGraph[AgentState, Any, AgentState, AgentState] = StateGraph(AgentState)
        builder.add_node("reason", self._reason)
        builder.add_node("act", self._act)
        builder.add_edge(START, "reason")
        builder.add_conditional_edges("reason", self._route, {"act": "act", END: END})
        builder.add_edge("act", "reason")
        return builder.compile()

    def _route(self, state: AgentState) -> str:
        """Continue to the tools only if the model asked for them and we may.

        The step cap is checked here rather than in ``reason`` so that hitting it
        ends the run cleanly with whatever the model last said, instead of
        raising into the request.
        """
        last = state["messages"][-1]
        wants_tools = last.get("role") == "assistant" and any(
            block.get("type") == "tool_use"
            for block in last.get("content", [])
            if isinstance(block, dict)
        )
        if not wants_tools:
            return END
        if state["steps"] >= self._max_steps:
            _logger.warning(
                "agent.step_limit_reached",
                extra={"steps": state["steps"], "max_steps": self._max_steps},
            )
            return END
        return "act"

    async def _reason(self, state: AgentState) -> dict[str, Any]:
        """Call the model, streaming its text into the custom stream.

        Not decorated with ``@traced``: LangGraph inspects node signatures, and
        the model call is already logged by the client.
        """
        # A no-op outside a streaming run, which is what lets one graph serve
        # both `complete` and `stream`.
        writer = get_stream_writer()

        turn: AssistantTurn | None = None
        async for event in self._llm.stream(
            system=self._system_prompt,
            messages=state["messages"],
            tools=self._specifications,
            max_tokens=state["max_tokens"],
        ):
            if isinstance(event, TextDelta):
                if event.text:
                    writer(event.text)
            elif isinstance(event, TurnCompleted):
                turn = event.turn

        if turn is None:  # pragma: no cover - a client that yields no TurnCompleted is broken
            raise RuntimeError("LLM client finished without completing a turn")

        return {
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": list(turn.raw_content)},
            ],
            "answer": turn.text,
            "usage": turn.usage,
            "steps": state["steps"] + 1,
            "stop_reason": turn.stop_reason,
        }

    async def _act(self, state: AgentState) -> dict[str, Any]:
        """Execute every tool the last turn requested, in order.

        A failing tool returns an ``is_error`` result rather than raising: the
        model asked for something that did not work, and the useful response is
        to tell it so it can correct itself. A crash here would throw away a
        recoverable run — and the tokens already spent on it.
        """
        last = state["messages"][-1]
        results: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for block in last.get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name", ""))
            arguments = block.get("input")
            result, cited = await self._invoke_one(
                tool_use_id=str(block.get("id", "")),
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
            )
            results.append(result)
            citations.extend(cited)
        # All results go back in ONE user message. Splitting them across several
        # messages trains the model out of requesting parallel tool calls.
        return {
            "messages": [*state["messages"], {"role": "user", "content": results}],
            "citations": tuple(citations),
        }

    async def _invoke_one(
        self, *, tool_use_id: str, name: str, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], tuple[Citation, ...]]:
        """Run one tool, returning its wire-format result and what it cited.

        A failing tool cites nothing: an error result is not grounding, and
        reporting a source for an answer the tool never produced would be a lie
        the client has no way to detect.
        """
        try:
            result = await self._tools.invoke(name, arguments)
        except ToolError as exc:
            _logger.info("agent.tool_failed", extra={"tool": name, "error": str(exc)})
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": str(exc),
                "is_error": True,
            }, ()
        except Exception as exc:
            # An unexpected tool bug is ours, not the model's. Log it loudly with
            # the trace, but still hand the model an error result so the run can
            # finish and the user gets an answer rather than a 500.
            _logger.error("agent.tool_crashed", extra={"tool": name}, exc_info=exc)
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"tool {name!r} failed unexpectedly",
                "is_error": True,
            }, ()
        _logger.info(
            "agent.tool_succeeded",
            extra={"tool": name, "citations": len(result.citations)},
        )
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": result.content,
        }, result.citations

    def _initial(self, messages: Sequence[dict[str, Any]], max_tokens: int | None) -> AgentState:
        return {
            "messages": list(messages),
            "answer": "",
            "usage": TokenUsage(),
            "steps": 0,
            "stop_reason": None,
            "max_tokens": max_tokens or self._max_tokens,
            "citations": (),
        }

    @traced
    async def run(
        self, messages: Sequence[dict[str, Any]], *, max_tokens: int | None = None
    ) -> AgentState:
        """Run the loop to completion and return the final state."""
        return _as_state(await self._graph.ainvoke(self._initial(messages, max_tokens)))

    async def stream(
        self, messages: Sequence[dict[str, Any]], *, max_tokens: int | None = None
    ) -> AsyncIterator[str | AgentState]:
        """Yield assistant text as it is produced, then the final state.

        Every ``str`` is a piece of assistant text; the single trailing
        ``AgentState`` carries the usage the caller needs to report. The union
        return type is how an async generator expresses "many of A, then one B";
        the orchestrator that consumes it narrows with ``isinstance``.

        Not decorated with ``@traced``: async generators are exempt (CLAUDE.md).
        """
        state = self._initial(messages, max_tokens)
        final: AgentState = state
        async for mode, chunk in self._graph.astream(state, stream_mode=["custom", "values"]):
            if mode == "custom" and isinstance(chunk, str):
                yield chunk
            elif mode == "values":
                final = _as_state(chunk)
        yield final
