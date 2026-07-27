"""Generate the prose doc pages ``case-study.html`` and ``demo.html``.

The markdown under ``docs/`` is the single source of truth; this renders each to a
styled, self-contained HTML page at the repo root, through the **same**
``doc_render`` machinery ``build_architecture.py`` uses — one styling source, no
drift between pages (ADR 0010: no CDN, no JS, works offline).

    uv run python scripts/build_docs.py           # write both pages
    uv run python scripts/build_docs.py --check    # exit 1 if either is out of date

A separate script from ``build_architecture.py`` on purpose: these pages carry **no
Mermaid diagrams**, so they need none of that script's diagram hashing, ``docs/
diagrams/`` handling, or Node/mermaid-cli ``--render`` path. Keeping them apart
means this script — and its ``--check``, run in CI and the test suite — is pure
Python with a single dependency (``markdown-it-py``), and neither script's contract
has to grow an "and also the other kind of page" branch. ``--check`` here is the
same drift guard ``tests/unit/test_architecture.py`` applies to
``architecture.html``, mirrored in ``tests/unit/test_docs_render.py``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import doc_render  # sibling module in scripts/ (mypy_path); shared page styling.

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_REL = "scripts/build_docs.py"
_REGENERATE = "uv run python scripts/build_docs.py"
_HEAD_NOTE = "Pre-rendered from its markdown source (ADR 0010)."


class _Page:
    """One markdown source → one generated HTML page at the repo root."""

    def __init__(self, source_rel: str, output_name: str, title_default: str) -> None:
        self.source_rel = source_rel
        self.source = _REPO_ROOT / source_rel
        self.output = _REPO_ROOT / output_name
        self.title_default = title_default

    def build(self, *, built: str) -> str:
        source_text = self.source.read_text(encoding="utf-8")
        return doc_render.render_page(
            source_text=source_text,
            body_html=doc_render.render_markdown(source_text),
            built=built,
            source_rel=self.source_rel,
            script_rel=_SCRIPT_REL,
            regenerate=_REGENERATE,
            head_note=_HEAD_NOTE,
            title_default=self.title_default,
        )


_PAGES = (
    _Page("docs/case-study.md", "case-study.html", "Case study — production-llm-platform"),
    _Page("docs/demo.md", "demo.html", "Demo — production-llm-platform"),
)


def _check_one(page: _Page) -> bool:
    """True when ``page`` is up to date; prints the reason to stderr when not."""
    if not page.output.is_file():
        print(f"{page.output.name} is missing at {page.output}", file=sys.stderr)
        print(f"Run: {_REGENERATE}", file=sys.stderr)
        return False
    current = page.output.read_text(encoding="utf-8")
    stamp = doc_render.recover_build_stamp(current, script_rel=_SCRIPT_REL)
    if stamp is None:
        print(f"{page.output.name} is malformed (no build stamp)", file=sys.stderr)
        print(f"Run: {_REGENERATE}", file=sys.stderr)
        return False
    # Reuse the recorded stamp so only real content changes count as drift.
    if page.build(built=stamp) != current:
        print(f"{page.output.name} is out of date with {page.source_rel}", file=sys.stderr)
        print(f"Run: {_REGENERATE}", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the generated pages match their markdown; do not write",
    )
    args = parser.parse_args()

    if args.check:
        # Evaluate every page (not short-circuited) so both are reported at once.
        results = [_check_one(page) for page in _PAGES]
        if all(results):
            print("case-study.html and demo.html are up to date.")
            return 0
        return 1

    built = datetime.now(UTC).strftime("%Y-%m-%d")
    for page in _PAGES:
        page.output.write_text(page.build(built=built), encoding="utf-8")
        print(f"Wrote {page.output.name} from {page.source_rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
