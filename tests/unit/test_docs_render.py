"""The generated prose pages (case-study.html, demo.html) cannot drift or fetch.

Mirrors ``tests/unit/test_architecture.py`` for the two Stage 10 pages rendered by
``scripts/build_docs.py`` through the shared ``doc_render`` module:

* **No drift** — editing the markdown without regenerating fails, the same guard
  ``architecture.html`` already has.
* **No dependencies** — ADR 0010: the pages load no CDN and run no JavaScript, so
  they render offline.
* **One styling source** — the shared CSS block is not re-declared in a script.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "build_docs.py"

_PAGES = {
    "case-study.html": "docs/case-study.md",
    "demo.html": "docs/demo.md",
}


def _html(name: str) -> str:
    return (_REPO_ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", _PAGES)
def test_page_exists(name: str) -> None:
    assert (_REPO_ROOT / name).is_file(), f"{name} is missing — run scripts/build_docs.py"


def test_pages_are_up_to_date_with_their_markdown() -> None:
    """Fails when a source .md was edited without regenerating its .html."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"a generated doc page is stale.\n{result.stdout}{result.stderr}"
        "Run: uv run python scripts/build_docs.py"
    )


@pytest.mark.parametrize(("name", "source"), _PAGES.items())
def test_generated_pages_are_marked_as_generated(name: str, source: str) -> None:
    """Anyone opening the HTML must be told not to edit it, and where the source is."""
    html = _html(name)
    assert "GENERATED FILE - DO NOT EDIT" in html
    assert source in html


class TestSelfContained:
    """ADR 0010: each page must render offline, with no network and no JS."""

    @pytest.mark.parametrize("name", _PAGES)
    def test_the_page_loads_no_external_resource(self, name: str) -> None:
        """No CDN/font/stylesheet/script fetch — the ADR 0010 offline property.

        Unlike architecture.html, these prose pages *quote* URLs as text in curl
        examples (``http://localhost:8000`` etc.); a browser never fetches those.
        What would break offline rendering is a URL in a *resource-loading*
        position — an attribute or CSS ``@import``/``url()`` — so that is what is
        asserted, not the mere mention of a URL.
        """
        html = _html(name)
        offenders = re.findall(
            r'(?:src|href)\s*=\s*["\']https?://|@import\s+["\']?https?://|url\(\s*["\']?https?://',
            html,
        )
        assert offenders == [], f"{name} loads external resources: {offenders}"

    @pytest.mark.parametrize("name", _PAGES)
    def test_the_page_runs_no_javascript(self, name: str) -> None:
        html = _html(name)
        assert "<script" not in html
        assert "cdn.jsdelivr" not in html


def test_the_pages_share_one_styling_source() -> None:
    """The CSS is defined once in doc_render and reused, never copied into a script.

    A duplicated ``:root`` block in build_docs.py is exactly the drift the shared
    module exists to prevent, so assert the styling lives only in doc_render.
    """
    build_docs = (_REPO_ROOT / "scripts" / "build_docs.py").read_text(encoding="utf-8")
    build_arch = (_REPO_ROOT / "scripts" / "build_architecture.py").read_text(encoding="utf-8")
    # The CSS custom-property block is the fingerprint of the style source.
    assert "--table-stripe:" not in build_docs
    assert "--table-stripe:" not in build_arch
    doc_render_src = (_REPO_ROOT / "scripts" / "doc_render.py").read_text(encoding="utf-8")
    assert "--table-stripe:" in doc_render_src

    # And both generated pages actually carry that shared CSS.
    for name in _PAGES:
        assert "--table-stripe:" in _html(name)
