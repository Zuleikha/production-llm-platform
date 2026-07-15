"""Tests for the agent's tool registry.

These test the tools' *contracts*: what they return, and how they fail. A tool's
failure mode matters as much as its result — the agent loop feeds errors back to
the model, so an unhelpful error message is a real defect, not cosmetics.
"""

from __future__ import annotations

import json

import pytest
from services.agents.tools import (
    Calculator,
    JsonQuery,
    TextStats,
    ToolError,
    ToolRegistry,
)


class TestCalculator:
    def test_evaluates_arithmetic(self) -> None:
        assert Calculator().run(expression="(2 + 3) * 4") == "20"

    def test_respects_operator_precedence(self) -> None:
        assert Calculator().run(expression="2 + 3 * 4") == "14"

    def test_supports_unary_negation(self) -> None:
        assert Calculator().run(expression="-5 + 2") == "-3"

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param("__import__('os').system('echo pwned')", id="import"),
            pytest.param("open('/etc/passwd').read()", id="builtin-call"),
            pytest.param("[].__class__", id="attribute-access"),
            pytest.param("x + 1", id="name"),
        ],
    )
    def test_refuses_anything_that_is_not_arithmetic(self, expression: str) -> None:
        """The model is not a trusted caller: this must never become eval()."""
        with pytest.raises(ToolError):
            Calculator().run(expression=expression)

    def test_rejects_a_huge_exponent_rather_than_hanging(self) -> None:
        """`**` is allow-listed, which makes 2**10**10 a trivial DoS."""
        with pytest.raises(ToolError, match="exceeds the limit"):
            Calculator().run(expression="2 ** 999999")

    def test_reports_division_by_zero_as_a_tool_error(self) -> None:
        with pytest.raises(ToolError, match="division by zero"):
            Calculator().run(expression="1 / 0")

    def test_reports_a_syntax_error_usefully(self) -> None:
        with pytest.raises(ToolError, match="not a valid expression"):
            Calculator().run(expression="2 +")

    def test_rejects_a_non_string_expression(self) -> None:
        with pytest.raises(ToolError, match="must be a string"):
            Calculator().run(expression=42)


class TestTextStats:
    def test_counts_characters_words_and_lines(self) -> None:
        result = json.loads(TextStats().run(text="hello world\nsecond line"))
        assert result == {"characters": 23, "words": 4, "lines": 2}

    def test_empty_text_has_no_lines(self) -> None:
        assert json.loads(TextStats().run(text="")) == {
            "characters": 0,
            "words": 0,
            "lines": 0,
        }


class TestJsonQuery:
    _DOC = '{"user": {"name": "ada", "roles": ["admin", "dev"]}, "active": true}'

    def test_reads_a_nested_value(self) -> None:
        assert JsonQuery().run(document=self._DOC, path="user.name") == '"ada"'

    def test_indexes_into_a_list(self) -> None:
        assert JsonQuery().run(document=self._DOC, path="user.roles.1") == '"dev"'

    def test_an_empty_path_returns_the_whole_document(self) -> None:
        assert json.loads(JsonQuery().run(document=self._DOC, path="")) == json.loads(self._DOC)

    def test_reports_a_missing_key(self) -> None:
        with pytest.raises(ToolError, match="no key 'nope'"):
            JsonQuery().run(document=self._DOC, path="user.nope")

    def test_reports_an_out_of_range_index(self) -> None:
        with pytest.raises(ToolError, match="out of range"):
            JsonQuery().run(document=self._DOC, path="user.roles.9")

    def test_reports_a_non_numeric_list_index(self) -> None:
        with pytest.raises(ToolError, match="not a list index"):
            JsonQuery().run(document=self._DOC, path="user.roles.first")

    def test_reports_invalid_json(self) -> None:
        with pytest.raises(ToolError, match="not valid JSON"):
            JsonQuery().run(document="{not json", path="a")

    def test_reports_descending_into_a_scalar(self) -> None:
        with pytest.raises(ToolError, match="cannot descend into"):
            JsonQuery().run(document=self._DOC, path="active.nope")


class TestToolRegistry:
    def test_default_registry_exposes_the_three_stage_three_tools(self) -> None:
        assert {tool.name for tool in ToolRegistry.default()} == {
            "calculator",
            "text_stats",
            "json_query",
        }

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

    def test_invoke_dispatches_by_name(self) -> None:
        assert ToolRegistry.default().invoke("calculator", {"expression": "6*7"}) == "42"

    def test_membership_and_length(self) -> None:
        registry = ToolRegistry.default()
        assert "calculator" in registry
        assert "teleport" not in registry
        assert len(registry) == 3
