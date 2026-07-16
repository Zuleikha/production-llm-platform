"""The tool registry: what the agent is allowed to do.

The tools **in this module** are deterministic, offline pure functions of their
arguments. That is what lets the agent loop be exercised end to end in a hermetic
suite. ``ToolRegistry.default()`` returns exactly those, and nothing here reaches
the network.

Stage 4 adds the first tool that is *not* a pure function — document retrieval,
which embeds a query with Voyage and searches Qdrant. It deliberately lives in
:mod:`services.retrieval.tool` rather than here, and is injected into the
registry at wiring time (see ``services.api.app``). The dependency runs
retrieval -> agents, which keeps this module the generic agent kernel: it knows
what a tool *is*, not what any particular tool talks to.

Tool results are **untrusted input** — and from Stage 4 that is a live concern
rather than a latent one. A tool's result text goes straight back into the
model's context, so a tool that reads attacker-controlled data can carry an
injected instruction with it. Through Stage 3 every tool returned values derived
solely from its own arguments, which is what made feeding results back unexamined
safe. Retrieval returns **document text**, which is exactly the case that breaks
that assumption. See ``README.md`` and ADR 0014 for the threat and the mitigation
applied; anyone adding a tool that reads outside data must read those first.
"""

from __future__ import annotations

import ast
import json
import operator
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from shared.observability import traced

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


class ToolError(Exception):
    """A tool failed on input the model supplied.

    Distinct from a bug: this is the expected way a tool says "that argument
    doesn't work". The loop feeds it back to the model as an error result so it
    can correct itself, rather than aborting the run.
    """


@dataclass(frozen=True, slots=True)
class Citation:
    """A source a tool consulted to produce its result.

    Provenance, carried out of the tool as **typed data** rather than left for
    someone to parse back out of the result text. Text is what the model reads;
    this is what the client gets. Deriving one from the other in either direction
    is how a citation ends up pointing at something the answer did not use.
    See ADR 0013.
    """

    id: str
    document_id: str
    source: str
    score: float
    text: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool hands back: text for the model, citations for the client.

    ``citations`` is empty for every tool that is a pure function of its
    arguments — there is no source to cite when the answer was computed rather
    than looked up.
    """

    content: str
    citations: tuple[Citation, ...] = field(default_factory=tuple)


class Tool(ABC):
    """One capability the agent can invoke.

    ``input_schema`` is a JSON Schema object; it is sent to the model verbatim
    as the tool's parameter contract, so it must describe the exact keyword
    arguments ``run`` accepts.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier the model uses to call this tool."""

    @property
    @abstractmethod
    def description(self) -> str:
        """What the tool does and when to reach for it.

        The model routes on this text, so it is behaviour, not documentation.
        """

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """JSON Schema for ``run``'s keyword arguments."""

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        """Execute the tool and return a result for the model to read.

        Async since Stage 4: retrieval has to embed a query and search a vector
        store, both of which are I/O. The three pure tools below gain nothing
        from it, but one contract the loop can await beats two contracts and a
        dispatch on which kind a tool is. The *loop* is unchanged — ``act`` still
        runs every requested tool and appends the results.

        Raises:
            ToolError: when the supplied arguments are unusable.
        """


def _require_str(kwargs: dict[str, Any], key: str) -> str:
    value = kwargs.get(key)
    if not isinstance(value, str):
        raise ToolError(f"{key!r} must be a string, got {type(value).__name__}")
    return value


class Calculator(Tool):
    """Evaluate an arithmetic expression.

    Parses to an AST and walks it against an allow-list rather than calling
    ``eval``. ``eval`` on model-supplied text is arbitrary code execution — the
    model is not a trusted caller, and neither is anything that talked to it.
    """

    _BINARY: Final[dict[type[ast.operator], Any]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    _UNARY: Final[dict[type[ast.unaryop], Any]] = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }
    # Bounds exponentiation: 2**10**10 is a trivially reachable way to hang the
    # process on an allow-listed operator.
    _MAX_EXPONENT: Final[int] = 64

    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return (
            "Evaluate an arithmetic expression and return the result. "
            "Supports + - * / // % ** and parentheses over numeric literals. "
            "Use this whenever a question requires exact arithmetic rather than "
            "an estimate."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The arithmetic expression, e.g. '(2 + 3) * 4'.",
                }
            },
            "required": ["expression"],
        }

    @traced
    async def run(self, **kwargs: Any) -> ToolResult:
        expression = _require_str(kwargs, "expression")
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ToolError(f"{expression!r} is not a valid expression: {exc.msg}") from exc
        return ToolResult(content=str(self._eval(tree.body)))

    def _eval(self, node: ast.expr) -> float | int:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int | float):
                raise ToolError(f"{node.value!r} is not a number")
            return node.value
        if isinstance(node, ast.BinOp):
            op = self._BINARY.get(type(node.op))
            if op is None:
                raise ToolError(f"{type(node.op).__name__} is not a supported operator")
            left, right = self._eval(node.left), self._eval(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > self._MAX_EXPONENT:
                raise ToolError(f"exponent {right} exceeds the limit of {self._MAX_EXPONENT}")
            try:
                result = op(left, right)
            except ZeroDivisionError as exc:
                raise ToolError("division by zero") from exc
            # Guard against complex results from e.g. (-8) ** 0.5.
            if not isinstance(result, int | float):
                raise ToolError(f"{expression_of(node)} did not produce a real number")
            return result
        if isinstance(node, ast.UnaryOp):
            unary = self._UNARY.get(type(node.op))
            if unary is None:
                raise ToolError(f"{type(node.op).__name__} is not a supported operator")
            negated = unary(self._eval(node.operand))
            if not isinstance(negated, int | float):  # pragma: no cover - +/- preserve numbers
                raise ToolError(f"{expression_of(node)} did not produce a real number")
            return negated
        raise ToolError(f"{type(node).__name__} is not allowed in an expression")


def expression_of(node: ast.expr) -> str:
    """Render a node back to source, for error messages."""
    return ast.unparse(node)


class TextStats(Tool):
    """Count characters, words and lines in a block of text."""

    @property
    def name(self) -> str:
        return "text_stats"

    @property
    def description(self) -> str:
        return (
            "Return the character, word and line counts of a block of text as "
            "JSON. Use this instead of counting by hand when a question asks "
            "how long a piece of text is."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The text to measure."}},
            "required": ["text"],
        }

    @traced
    async def run(self, **kwargs: Any) -> ToolResult:
        text = _require_str(kwargs, "text")
        return ToolResult(
            content=json.dumps(
                {
                    "characters": len(text),
                    "words": len(text.split()),
                    # An empty string is zero lines; anything else has at least one.
                    "lines": len(text.splitlines()) if text else 0,
                }
            )
        )


class JsonQuery(Tool):
    """Read a single value out of a JSON document by dotted path."""

    @property
    def name(self) -> str:
        return "json_query"

    @property
    def description(self) -> str:
        return (
            "Extract one value from a JSON document using a dotted path, e.g. "
            "'user.roles.0'. Returns the value as JSON. Use this to read a "
            "specific field out of a JSON payload rather than eyeballing it."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "document": {"type": "string", "description": "The JSON document as text."},
                "path": {
                    "type": "string",
                    "description": (
                        "Dotted path to the value. Use numeric segments to index "
                        "arrays, e.g. 'items.0.name'."
                    ),
                },
            },
            "required": ["document", "path"],
        }

    @traced
    async def run(self, **kwargs: Any) -> ToolResult:
        document = _require_str(kwargs, "document")
        path = _require_str(kwargs, "path")
        try:
            current: Any = json.loads(document)
        except json.JSONDecodeError as exc:
            raise ToolError(f"document is not valid JSON: {exc.msg}") from exc

        for segment in filter(None, path.split(".")):
            current = self._descend(current, segment, path)
        return ToolResult(content=json.dumps(current))

    @staticmethod
    def _descend(current: Any, segment: str, path: str) -> Any:
        if isinstance(current, dict):
            if segment not in current:
                raise ToolError(f"{path!r}: no key {segment!r} at this level")
            return current[segment]
        if isinstance(current, list):
            if not segment.lstrip("-").isdigit():
                raise ToolError(f"{path!r}: {segment!r} is not a list index")
            index = int(segment)
            try:
                return current[index]
            except IndexError as exc:
                raise ToolError(f"{path!r}: index {index} is out of range") from exc
        raise ToolError(f"{path!r}: cannot descend into {type(current).__name__}")


class ToolRegistry:
    """The set of tools an agent may call, keyed by name."""

    def __init__(self, tools: Sequence[Tool]) -> None:
        registry: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in registry:
                raise ValueError(f"duplicate tool name: {tool.name!r}")
            registry[tool.name] = tool
        self._tools = registry

    @classmethod
    def default(cls) -> ToolRegistry:
        """The tools every agent gets unless a caller says otherwise.

        Offline and deterministic, every one. Retrieval is **not** here: it needs
        a live ``Retriever``, and a default that silently did no retrieval would
        be worse than one that has none. ``with_tools`` is how it gets added.
        """
        return cls([Calculator(), TextStats(), JsonQuery()])

    def with_tools(self, *tools: Tool) -> ToolRegistry:
        """Return a new registry: this one's tools plus ``tools``.

        How the retrieval tool joins the set the agent may call, without
        ``services.agents`` having to know retrieval exists. Returns a new
        registry rather than mutating: a registry is rendered into the model's
        tool specifications once at graph construction, and a set that could
        change afterwards would silently disagree with what the model was told.

        Raises:
            ValueError: on a duplicate tool name.
        """
        return ToolRegistry([*self._tools.values(), *tools])

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool:
        """Return the tool called ``name``.

        Raises:
            ToolError: if no such tool is registered. This is a ToolError rather
                than a KeyError because a hallucinated tool name is the model's
                mistake to correct, not a crash.
        """
        tool = self._tools.get(name)
        if tool is None:
            known = ", ".join(sorted(self._tools)) or "(none)"
            raise ToolError(f"no tool named {name!r}. Available tools: {known}")
        return tool

    @traced
    def specifications(self) -> list[dict[str, Any]]:
        """Render every tool into the Anthropic tool-definition wire format.

        Sorted by name so the rendered list is byte-stable across processes —
        an unstable tool order would invalidate the prompt cache on every call.
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    @traced
    async def invoke(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Run tool ``name`` with ``arguments``, returning its result.

        Raises:
            ToolError: if the tool is unknown or the arguments are unusable.
        """
        return await self.get(name).run(**arguments)
