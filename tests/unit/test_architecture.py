"""Tests that the generated architecture.html cannot drift from its source.

Same guard as ``test_runtime_version_matches_pyproject``: a doc that is
generated from another file is only trustworthy if something fails when the two
disagree.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "build_architecture.py"
_HTML = _REPO_ROOT / "architecture.html"
_MARKDOWN = _REPO_ROOT / "docs" / "architecture.md"


def test_architecture_html_exists() -> None:
    assert _HTML.is_file(), "architecture.html is missing — run scripts/build_architecture.py"


def test_architecture_html_is_up_to_date_with_the_markdown() -> None:
    """Fails when docs/architecture.md was edited without regenerating the HTML."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"architecture.html is stale.\n{result.stdout}{result.stderr}"
        "Run: uv run python scripts/build_architecture.py"
    )


def test_generated_html_is_marked_as_generated() -> None:
    """Anyone opening the HTML must be told not to edit it."""
    html = _HTML.read_text(encoding="utf-8")
    assert "GENERATED FILE - DO NOT EDIT" in html
    assert "docs/architecture.md" in html


def test_a_mermaid_load_failure_is_reported_not_silent() -> None:
    """Offline, diagrams degrade to source text — the page must say why.

    A static import cannot be caught, so a dynamic import inside try/catch is the
    thing that makes the failure visible. Pin that, not just the notice's text.
    """
    html = _HTML.read_text(encoding="utf-8")
    assert 'id="diagram-notice"' in html
    assert "internet access" in html
    assert "await import(" in html, "a static import cannot report its own failure"
    assert "catch (error)" in html
    assert 'getElementById("diagram-notice").hidden = false' in html


def test_the_failure_notice_is_hidden_when_diagrams_render() -> None:
    """It must not cry wolf on the happy path."""
    html = _HTML.read_text(encoding="utf-8")
    assert '<div id="diagram-notice" class="diagram-notice" hidden>' in html


def test_diagrams_are_explained_when_javascript_is_disabled() -> None:
    """<noscript> covers JS-off; it does NOT fire when JS runs but the CDN is down."""
    html = _HTML.read_text(encoding="utf-8")
    assert "<noscript>" in html
    assert "JavaScript is\n  disabled" in html or "JavaScript is disabled" in html


def test_mermaid_diagrams_are_rendered_not_shown_as_code() -> None:
    """The whole point of the HTML view is drawn diagrams.

    A mermaid fence must become a <pre class="mermaid"> block for the library to
    render; if it renders as a plain code block the diagram never draws.
    """
    assert "```mermaid" in _MARKDOWN.read_text(encoding="utf-8"), (
        "source has no mermaid diagrams — this test guards the wrong thing now"
    )
    html = _HTML.read_text(encoding="utf-8")
    assert '<pre class="mermaid">' in html
