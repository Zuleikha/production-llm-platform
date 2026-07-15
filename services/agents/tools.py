"""The tool registry: what the agent is allowed to do.

Every tool here is **deterministic and offline**. That is a deliberate
constraint, not a placeholder: the agent loop is exercised end to end in the
test suite, and a tool that reaches a live external API would make those tests
flaky and non-hermetic. Tools that call the network arrive with the stages that
own them — retrieval/vector search is Stage 4 and stays out of this registry.

Tools are also domain-agnostic: generic engineering utilities, not a named
product's operations. The point of the stage is the *loop*, and a narrow domain
tool would prove less about it.

Tool results are **untrusted input**. A tool returns text that goes straight
back into the model's context, so a tool that read attacker-controlled data
could carry an injected instruction with it. The tools here only ever return
values derived from their own arguments, which is what makes that safe today —
Stage 4 changes that and must revisit it.
"""

from __future__ import annotations

import ast
import json
import operator
from abc import ABC, abstractmethod
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
    def run(self, **kwargs: Any) -> str:
        """Execute the tool and return a result for the model to read.

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
    def run(self, **kwargs: Any) -> str:
        expression = _require_str(kwargs, "expression")
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ToolError(f"{expression!r} is not a valid expression: {exc.msg}") from exc
        return str(self._eval(tree.body))

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
    def run(self, **kwargs: Any) -> str:
        text = _require_str(kwargs, "text")
        return json.dumps(
            {
                "characters": len(text),
                "words": len(text.split()),
                # An empty string is zero lines; anything else has at least one.
                "lines": len(text.splitlines()) if text else 0,
            }
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
    def run(self, **kwargs: Any) -> str:
        document = _require_str(kwargs, "document")
        path = _require_str(kwargs, "path")
        try:
            current: Any = json.loads(document)
        except json.JSONDecodeError as exc:
            raise ToolError(f"document is not valid JSON: {exc.msg}") from exc

        for segment in filter(None, path.split(".")):
            current = self._descend(current, segment, path)
        return json.dumps(current)

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
        """The tools every agent gets unless a caller says otherwise."""
        return cls([Calculator(), TextStats(), JsonQuery()])

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
    def invoke(self, name: str, arguments: dict[str, Any]) -> str:
        """Run tool ``name`` with ``arguments``, returning its result text.

        Raises:
            ToolError: if the tool is unknown or the arguments are unusable.
        """
        return self.get(name).run(**arguments)
