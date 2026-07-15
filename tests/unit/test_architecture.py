"""Tests that the generated architecture.html cannot drift, and needs nothing.

Two guarantees are pinned here.

**No drift.** Same guard as ``test_runtime_version_matches_pyproject``: a doc
generated from another file is only trustworthy if something fails when the two
disagree.

**No dependencies.** From Stage 3 the diagrams are pre-rendered to inline SVG and
the page loads no CDN and runs no JavaScript (ADR 0010). The Stage 2 tests that
used to live here pinned the *opposite* — a Mermaid CDN import and a "you need
internet access" notice for when it failed. That machinery is gone, so those
tests are gone with it; these replace them by asserting the property that made
it unnecessary.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "build_architecture.py"
_HTML = _REPO_ROOT / "architecture.html"
_MARKDOWN = _REPO_ROOT / "docs" / "architecture.md"
_DIAGRAMS = _REPO_ROOT / "docs" / "diagrams"


def _html() -> str:
    return _HTML.read_text(encoding="utf-8")


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
    html = _html()
    assert "GENERATED FILE - DO NOT EDIT" in html
    assert "docs/architecture.md" in html


class TestSelfContained:
    """ADR 0010: the page must render offline, with no network and no JS."""

    def test_the_page_fetches_nothing(self) -> None:
        """A doc that only works online fails exactly when someone is debugging."""
        html = _html()
        # XML namespace declarations are identifiers, not fetches.
        urls = [
            url for url in re.findall(r'https?://[^\s"\'<>)]+', html) if "www.w3.org" not in url
        ]
        assert urls == [], f"architecture.html references external resources: {urls}"

    def test_the_page_runs_no_javascript(self) -> None:
        html = _html()
        assert "<script" not in html
        assert "cdn.jsdelivr" not in html
        assert "mermaid.esm" not in html

    def test_diagrams_are_inline_svg_not_mermaid_source(self) -> None:
        """The whole point of the HTML view is drawn diagrams."""
        assert "```mermaid" in _MARKDOWN.read_text(encoding="utf-8"), (
            "source has no mermaid diagrams — this test guards the wrong thing now"
        )
        html = _html()
        assert "<svg" in html
        # The Stage 2 shape: source handed to a client-side library.
        assert '<pre class="mermaid">' not in html

    def test_every_markdown_diagram_reaches_the_page(self) -> None:
        sys.path.insert(0, str(_REPO_ROOT / "scripts"))
        from build_architecture import _mermaid_blocks

        expected = len(_mermaid_blocks(_MARKDOWN.read_text(encoding="utf-8")))

        assert expected > 0
        assert _html().count("<svg") == expected


class TestRenderedDiagrams:
    def test_every_rendered_diagram_is_committed(self) -> None:
        """Building the page must not depend on a JS toolchain — only rendering does."""
        sys.path.insert(0, str(_REPO_ROOT / "scripts"))
        from build_architecture import _diagram_path, _mermaid_blocks

        missing = [
            _diagram_path(block).name
            for block in _mermaid_blocks(_MARKDOWN.read_text(encoding="utf-8"))
            if not _diagram_path(block).is_file()
        ]

        assert missing == [], f"unrendered diagrams: {missing}. Run scripts/build_architecture.py"

    def test_no_orphaned_diagrams_are_left_behind(self) -> None:
        """A stale SVG is a diagram of an architecture that no longer exists."""
        sys.path.insert(0, str(_REPO_ROOT / "scripts"))
        from build_architecture import _diagram_path, _mermaid_blocks

        wanted = {
            _diagram_path(block).name
            for block in _mermaid_blocks(_MARKDOWN.read_text(encoding="utf-8"))
        }
        on_disk = {p.name for p in _DIAGRAMS.glob("*.svg")}

        assert on_disk == wanted, f"orphaned: {sorted(on_disk - wanted)}"

    def test_building_the_page_never_shells_out_to_node(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The load-bearing claim of ADR 0010's design: --check needs only Python.

        CI and this suite must be able to detect drift without installing a
        JavaScript toolchain, so the build path that reads committed SVGs must
        not touch npx even when npx is absent.
        """
        sys.path.insert(0, str(_REPO_ROOT / "scripts"))
        import build_architecture

        # Patch the stdlib itself, not the module's names: the build resolves
        # both at call time, so this is what "npx is not installed" looks like.
        monkeypatch.setattr(shutil, "which", lambda _: None)

        def explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("building the page must not invoke a subprocess")

        monkeypatch.setattr(subprocess, "run", explode)

        html = build_architecture._build(built="2026-01-01", render=False)

        assert "<svg" in html


class TestLayering:
    """The dependency direction is api -> orchestrator -> agents. Never back."""

    @staticmethod
    def _imported_modules(path: Path) -> set[str]:
        """Every module this file imports, from the AST rather than by grepping.

        Grepping the source would match the module name in a docstring — and the
        docstrings here deliberately *name* the import they are avoiding.
        """
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        return modules

    def test_the_orchestrator_can_be_imported_without_the_api(self) -> None:
        """A regression test for a real circular import.

        `services/orchestrator/base.py` briefly imported `services.api.schemas`
        for its message type. Because `services/api/__init__` used to build the
        app, that made `import services.orchestrator` fail outright — but only
        when nothing had imported `services.api` first, which conftest always
        does. So the whole suite passed and a plain script did not.
        """
        result = subprocess.run(
            [sys.executable, "-c", "import services.orchestrator, services.agents"],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
        )

        assert result.returncode == 0, f"circular import returned:\n{result.stderr}"

    def test_the_orchestrator_does_not_import_the_api(self) -> None:
        """The layering rule itself, not just its symptom."""
        offenders = {
            path.relative_to(_REPO_ROOT).as_posix(): sorted(
                m for m in self._imported_modules(path) if m.startswith("services.api")
            )
            for path in (_REPO_ROOT / "services" / "orchestrator").rglob("*.py")
        }
        offenders = {k: v for k, v in offenders.items() if v}

        assert offenders == {}, (
            f"{offenders} import from services.api — the dependency runs "
            "api -> orchestrator. Convert at the API boundary instead."
        )

    def test_the_agents_package_does_not_import_the_api(self) -> None:
        offenders = {
            path.relative_to(_REPO_ROOT).as_posix(): sorted(
                m for m in self._imported_modules(path) if m.startswith("services.api")
            )
            for path in (_REPO_ROOT / "services" / "agents").rglob("*.py")
        }
        offenders = {k: v for k, v in offenders.items() if v}

        assert offenders == {}

    def test_importing_an_api_submodule_does_not_build_the_app(self) -> None:
        """A package __init__ that constructs the ASGI app is a landmine.

        It means importing `services.api.schemas` builds the whole app and
        everything it touches — which is what turned a bad import into a cycle.
        """
        init = _REPO_ROOT / "services" / "api" / "__init__.py"

        assert "services.api.app" not in self._imported_modules(init)
