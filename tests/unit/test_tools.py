"""Tests for the agent's tool registry.

These test the tools' *contracts*: what they return, and how they fail. A tool's
failure mode matters as much as its result — the agent loop feeds errors back to
the model, so an unhelpful error message is a real defect, not cosmetics.

Every tool is awaited from Stage 4: ``Tool.run`` became async so that retrieval,
which does I/O, could be a tool like any other rather than a special case in the
loop (see ``services/agents/tools.py``). The three tools here gain nothing from
it — they are still pure functions of their arguments — which is the point: one
contract, not two.
"""

from __future__ import annotations

import json

import pytest
from services.agents.tools import (
    Calculator,
    Citation,
    JsonQuery,
    TextStats,
    Tool,
    ToolError,
    ToolRegistry,
    ToolResult,
)


class TestCalculator:
    async def test_evaluates_arithmetic(self) -> None:
        assert (await Calculator().run(expression="(2 + 3) * 4")).content == "20"

    async def test_respects_operator_precedence(self) -> None:
        assert (await Calculator().run(expression="2 + 3 * 4")).content == "14"

    async def test_supports_unary_negation(self) -> None:
        assert (await Calculator().run(expression="-5 + 2")).content == "-3"

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param("__import__('os').system('echo pwned')", id="import"),
            pytest.param("open('/etc/passwd').read()", id="builtin-call"),
            pytest.param("[].__class__", id="attribute-access"),
            pytest.param("x + 1", id="name"),
        ],
    )
    async def test_refuses_anything_that_is_not_arithmetic(self, expression: str) -> None:
        """The model is not a trusted caller: this must never become eval()."""
        with pytest.raises(ToolError):
            await Calculator().run(expression=expression)

    async def test_rejects_a_huge_exponent_rather_than_hanging(self) -> None:
        """`**` is allow-listed, which makes 2**10**10 a trivial DoS."""
        with pytest.raises(ToolError, match="exceeds the limit"):
            await Calculator().run(expression="2 ** 999999")

    async def test_reports_division_by_zero_as_a_tool_error(self) -> None:
        with pytest.raises(ToolError, match="division by zero"):
            await Calculator().run(expression="1 / 0")

    async def test_reports_a_syntax_error_usefully(self) -> None:
        with pytest.raises(ToolError, match="not a valid expression"):
            await Calculator().run(expression="2 +")

    async def test_rejects_a_non_string_expression(self) -> None:
        with pytest.raises(ToolError, match="must be a string"):
            await Calculator().run(expression=42)

    async def test_a_pure_tool_cites_nothing(self) -> None:
        """Nothing was looked up, so there is no source to cite."""
        assert (await Calculator().run(expression="1+1")).citations == ()


class TestTextStats:
    async def test_counts_characters_words_and_lines(self) -> None:
        result = json.loads((await TextStats().run(text="hello world\nsecond line")).content)
        assert result == {"characters": 23, "words": 4, "lines": 2}

    async def test_empty_text_has_no_lines(self) -> None:
        assert json.loads((await TextStats().run(text="")).content) == {
            "characters": 0,
            "words": 0,
            "lines": 0,
        }


class TestJsonQuery:
    _DOC = '{"user": {"name": "ada", "roles": ["admin", "dev"]}, "active": true}'

    async def test_reads_a_nested_value(self) -> None:
        assert (await JsonQuery().run(document=self._DOC, path="user.name")).content == '"ada"'

    async def test_indexes_into_a_list(self) -> None:
        assert (await JsonQuery().run(document=self._DOC, path="user.roles.1")).content == '"dev"'

    async def test_an_empty_path_returns_the_whole_document(self) -> None:
        result = await JsonQuery().run(document=self._DOC, path="")
        assert json.loads(result.content) == json.loads(self._DOC)

    async def test_reports_a_missing_key(self) -> None:
        with pytest.raises(ToolError, match="no key 'nope'"):
            await JsonQuery().run(document=self._DOC, path="user.nope")

    async def test_reports_an_out_of_range_index(self) -> None:
        with pytest.raises(ToolError, match="out of range"):
            await JsonQuery().run(document=self._DOC, path="user.roles.9")

    async def test_reports_a_non_numeric_list_index(self) -> None:
        with pytest.raises(ToolError, match="not a list index"):
            await JsonQuery().run(document=self._DOC, path="user.roles.first")

    async def test_reports_invalid_json(self) -> None:
        with pytest.raises(ToolError, match="not valid JSON"):
            await JsonQuery().run(document="{not json", path="a")

    async def test_reports_descending_into_a_scalar(self) -> None:
        with pytest.raises(ToolError, match="cannot descend into"):
            await JsonQuery().run(document=self._DOC, path="active.nope")


class _StubTool(Tool):
    """A minimal Tool, for registry composition tests."""

    def __init__(self, name: str = "stub") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "a stub"

    @property
    def input_schema(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def run(self, **kwargs: object) -> ToolResult:
        return ToolResult(
            content="stubbed",
            citations=(Citation(id="c1", document_id="d", source="d", score=1.0, text="t"),),
        )


class TestToolRegistry:
    def test_default_registry_exposes_the_three_offline_tools(self) -> None:
        assert {tool.name for tool in ToolRegistry.default()} == {
            "calculator",
            "text_stats",
            "json_query",
        }

    def test_the_default_registry_has_no_retrieval_tool(self) -> None:
        """Retrieval needs a live Qdrant; a default that silently did none would lie."""
        assert "document_search" not in ToolRegistry.default()

    def test_rejects_duplicate_names(self) -> None:
        with pytest.raises(ValueError, match="duplicate tool name"):
            ToolRegistry([Calculator(), Calculator()])

    def test_unknown_tool_is_a_tool_error_naming_the_alternatives(self) -> None:
        """A hallucinated tool name is the model's mistake to correct, not a crash."""
        with pytest.raises(ToolError, match="calculator"):
            ToolRegistry.default().get("teleport")

    def test_specifications_match_the_anthropic_tool_format(self) -> None:
        specs = ToolRegistry.default().specifications()
        assert all(set(spec) == {"name", "description", "input_schema"} for spec in specs)
        assert all(spec["input_schema"]["type"] == "object" for spec in specs)

    def test_specifications_are_ordered_stably(self) -> None:
        """An unstable tool order would invalidate the prompt cache every call."""
        names = [spec["name"] for spec in ToolRegistry.default().specifications()]
        assert names == sorted(names)

    async def test_invoke_dispatches_by_name(self) -> None:
        result = await ToolRegistry.default().invoke("calculator", {"expression": "6*7"})
        assert result.content == "42"

    async def test_invoke_returns_a_tools_citations(self) -> None:
        registry = ToolRegistry([_StubTool()])
        result = await registry.invoke("stub", {})
        assert [c.id for c in result.citations] == ["c1"]

    def test_membership_and_length(self) -> None:
        registry = ToolRegistry.default()
        assert "calculator" in registry
        assert "teleport" not in registry
        assert len(registry) == 3

    def test_with_tools_adds_without_mutating_the_original(self) -> None:
        """The graph renders specifications once; a registry that could change
        underneath it would disagree with what the model was told."""
        base = ToolRegistry.default()
        extended = base.with_tools(_StubTool())

        assert len(extended) == 4
        assert "stub" in extended
        assert "stub" not in base
        assert len(base) == 3

    def test_with_tools_rejects_a_name_that_already_exists(self) -> None:
        with pytest.raises(ValueError, match="duplicate tool name"):
            ToolRegistry.default().with_tools(_StubTool(name="calculator"))
