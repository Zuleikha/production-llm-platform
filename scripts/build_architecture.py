"""Generate ``architecture.html`` from ``docs/architecture.md``.

The markdown is the single source of truth; this script renders a browsable view
of it with the Mermaid diagrams **pre-rendered to inline SVG**. Two documents
describing one architecture would drift, so there is only ever one to edit.

    uv run python scripts/build_architecture.py           # write architecture.html
    uv run python scripts/build_architecture.py --check   # exit 1 if out of date

``--check`` is what ``tests/unit/test_architecture.py`` and CI use to fail when
someone edits the markdown and forgets to regenerate — the same guard the repo
already applies to ``version.py`` vs ``pyproject.toml``.

Why pre-rendered SVG (ADR 0010): the page has **no external dependencies**. It
renders identically offline, in a locked-down browser, and in ten years when
whatever CDN we used has moved on. Stage 2 loaded Mermaid from a pinned CDN and
the diagrams were simply absent without a network — a documentation page that
only works online is a documentation page that fails exactly when someone is
diagnosing an outage.

**The rendering toolchain is not required to build the HTML.** Diagrams are
rendered once to committed files under ``docs/diagrams/`` and keyed by a hash of
their Mermaid source. Building the page — and therefore ``--check``, the test,
and CI — only inlines those files and needs nothing but Python. Node and
mermaid-cli are needed solely when a diagram's source actually changes, which is
what ``--render`` (the default when something is stale) does.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from markdown_it import MarkdownIt

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SOURCE = _REPO_ROOT / "docs" / "architecture.md"
_OUTPUT = _REPO_ROOT / "architecture.html"
_DIAGRAM_DIR = _REPO_ROOT / "docs" / "diagrams"

# Pinned: an unpinned renderer would let the diagrams change under us between
# builds, which is the same drift the committed SVGs exist to prevent.
_MERMAID_CLI = "@mermaid-js/mermaid-cli@11.4.2"

# Mermaid layout config passed to mermaid-cli on every render. It exists to fix a
# real defect: with mermaid-cli's default spacing, a state-diagram self-loop label
# and an adjacent edge label land in the same band and OVERPRINT (e.g. the
# datastore-lifecycle diagram's `ping ok -> "ok"` printed on top of `driver pool
# reconnects`), and tight flowchart spacing crowds multi-line labels. Widening
# nodeSpacing / rankSpacing / padding — and, for state diagrams, edgeLengthFactor —
# gives every label room, so the fix lives in the renderer, not in shortened
# labels. It applies to every diagram this module renders: the live architecture.md
# and any historical snapshot driven through the same functions.
_MERMAID_CONFIG = _REPO_ROOT / "scripts" / "mermaid-config.json"

_GENERATED_BANNER = "GENERATED FILE - DO NOT EDIT"

_CSS = """
:root {
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --border: #d1d9e0;
  --accent: #0969da; --code-bg: #f6f8fa; --quote-bg: #f6f8fa;
  --table-stripe: #f6f8fa;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --border: #3d444d;
    --accent: #4493f8; --code-bg: #151b23; --quote-bg: #151b23;
    --table-stripe: #151b23;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1rem 6rem; background: var(--bg); color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans",
               Helvetica, Arial, sans-serif;
  line-height: 1.6; font-size: 16px;
}
main { max-width: 980px; margin: 0 auto; }
h1, h2, h3 { line-height: 1.25; margin-top: 2rem; margin-bottom: 1rem; font-weight: 600; }
h1 { font-size: 2rem; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
h2 { font-size: 1.5rem; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
h3 { font-size: 1.25rem; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code {
  background: var(--code-bg); padding: .2em .4em; border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: .875em;
}
pre {
  background: var(--code-bg); padding: 1rem; border-radius: 6px;
  overflow-x: auto; max-width: 100%;
}
pre code { background: none; padding: 0; }
blockquote {
  margin: 1rem 0; padding: .75rem 1rem; border-left: .25em solid var(--border);
  background: var(--quote-bg); color: var(--muted); border-radius: 0 6px 6px 0;
}
blockquote > :first-child { margin-top: 0; }
blockquote > :last-child { margin-bottom: 0; }
/* Wide tables scroll inside their own container so the page never does. */
.table-scroll { overflow-x: auto; margin: 1rem 0; }
table { border-collapse: collapse; width: 100%; }
th, td {
  border: 1px solid var(--border); padding: .5rem .75rem;
  text-align: left; vertical-align: top;
}
th { background: var(--table-stripe); font-weight: 600; }
tr:nth-child(2n) td { background: var(--table-stripe); }
hr { border: 0; border-top: 1px solid var(--border); margin: 2rem 0; }
/* Diagrams keep their natural size and scroll inside this box, so the page body
   never scrolls sideways. The SVG is inlined — no script, no network. */
.diagram {
  margin: 1.5rem 0; padding: 1rem; overflow-x: auto;
  border: 1px solid var(--border); border-radius: 6px; background: #ffffff;
}
.diagram svg { display: block; margin: 0 auto; height: auto; max-width: none; }
/* The diagrams are rendered on a light canvas, so they keep a white plate in
   dark mode rather than becoming unreadable dark-on-dark. */
@media (prefers-color-scheme: dark) {
  .diagram { background: #f6f8fa; }
}
.build-stamp {
  margin: 0 auto 2rem; max-width: 980px; color: var(--muted); font-size: .8125rem;
  border: 1px dashed var(--border); border-radius: 6px; padding: .5rem .75rem;
}
.build-stamp code { font-size: .8125em; }
"""

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<!-- {banner}. Source: docs/architecture.md
     Regenerate: uv run python scripts/build_architecture.py
     Diagrams are pre-rendered inline SVG (docs/diagrams/). No CDN, no JS. -->
<style>{css}</style>
</head>
<body>
<div class="build-stamp">
  <strong>Generated file — do not edit.</strong>
  Rendered from <code>docs/architecture.md</code> by
  <code>scripts/build_architecture.py</code>. Built {built}.
</div>
<main>
{body}
</main>
</body>
</html>
"""


def _slug(text: str) -> str:
    """A stable, filesystem-safe name for a diagram, from its first line."""
    first = next((line for line in text.splitlines() if line.strip()), "diagram")
    cleaned = re.sub(r"[^a-z0-9]+", "-", first.lower()).strip("-")
    return cleaned[:40] or "diagram"


def _config_fingerprint() -> str:
    """A short hash of the Mermaid config, or empty string when there is none.

    Folded into the diagram digest so that changing the layout config
    (``mermaid-config.json``) invalidates every cached SVG — otherwise a config
    change would silently keep serving the stale renders it was meant to replace,
    which is exactly the caching trap that let the overlap bug persist.
    """
    if not _MERMAID_CONFIG.is_file():
        return ""
    normalised = _MERMAID_CONFIG.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:8]


def _digest(source: str) -> str:
    """Key a rendered SVG to its exact Mermaid source *and* the layout config.

    Newlines are normalised first so a checkout with different line endings does
    not read as a changed diagram — this repo is developed on Windows and built
    in Linux CI. The config fingerprint is mixed in so the cache key covers
    everything that affects the rendered output, not just the diagram text.
    """
    normalised = source.replace("\r\n", "\n").strip()
    keyed = f"{normalised}\x00{_config_fingerprint()}"
    return hashlib.sha256(keyed.encode("utf-8")).hexdigest()[:12]


def _diagram_path(source: str) -> Path:
    return _DIAGRAM_DIR / f"{_slug(source)}-{_digest(source)}.svg"


def _mermaid_blocks(text: str) -> list[str]:
    """Every mermaid fence in the markdown, in order."""
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    return [
        token.content
        for token in md.parse(text)
        if token.type == "fence" and token.info.strip().lower() == "mermaid"
    ]


def _render_svg(source: str, destination: Path) -> None:
    """Render one Mermaid diagram to ``destination`` via mermaid-cli.

    Raises:
        RuntimeError: if the toolchain is missing or the render fails. Loud on
            purpose — a silently skipped diagram would ship a page with a hole
            in it.
    """
    npx = shutil.which("npx")
    if npx is None:
        raise RuntimeError(
            f"cannot render {destination.name}: npx not found on PATH.\n"
            "Rendering diagrams needs Node (https://nodejs.org). Note this is only "
            "required when a diagram's Mermaid source changes — building the HTML "
            "from already-rendered diagrams needs nothing but Python."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        mmd = Path(tmp) / "diagram.mmd"
        mmd.write_text(source, encoding="utf-8")
        command = [
            npx,
            "-y",
            _MERMAID_CLI,
            "--input",
            str(mmd),
            "--output",
            str(destination),
            "--outputFormat",
            "svg",
            "--backgroundColor",
            "transparent",
        ]
        # The layout config that fixes label overlap (see _MERMAID_CONFIG). Passed
        # only when present, so the renderer still works if it is ever removed.
        if _MERMAID_CONFIG.is_file():
            command += ["--configFile", str(_MERMAID_CONFIG)]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0 or not destination.is_file():
        raise RuntimeError(
            f"mermaid-cli failed to render {destination.name} "
            f"(exit {result.returncode}):\n{result.stderr.strip()}"
        )


def _sanitise(svg: str) -> str:
    """Strip the XML prolog and doctype so the SVG can be inlined into HTML."""
    svg = re.sub(r"<\?xml[^>]*\?>\s*", "", svg)
    svg = re.sub(r"<!DOCTYPE[^>]*>\s*", "", svg, flags=re.IGNORECASE)
    return svg.strip()


def _ensure_diagrams(text: str, *, render: bool) -> dict[str, str]:
    """Return ``{mermaid source: inline svg}``, rendering what is missing.

    Raises:
        RuntimeError: when a diagram is missing and ``render`` is False. That is
            the ``--check`` path: it means the markdown changed without the
            diagrams being regenerated, which is exactly the drift to catch.
    """
    diagrams: dict[str, str] = {}
    for source in _mermaid_blocks(text):
        path = _diagram_path(source)
        if not path.is_file():
            if not render:
                raise RuntimeError(
                    f"no rendered diagram for {path.name}: docs/architecture.md has a "
                    "diagram that has not been rendered.\n"
                    "Run: uv run python scripts/build_architecture.py"
                )
            _render_svg(source, path)
            print(f"  rendered {path.relative_to(_REPO_ROOT)}")
        diagrams[source] = _sanitise(path.read_text(encoding="utf-8"))
    return diagrams


def _prune_orphans(text: str) -> list[Path]:
    """Delete rendered diagrams no longer referenced by the markdown."""
    if not _DIAGRAM_DIR.is_dir():
        return []
    wanted = {_diagram_path(source).name for source in _mermaid_blocks(text)}
    removed: list[Path] = []
    for path in sorted(_DIAGRAM_DIR.glob("*.svg")):
        if path.name not in wanted:
            path.unlink()
            removed.append(path)
    return removed


def _render_markdown(text: str, diagrams: dict[str, str]) -> str:
    """Convert markdown to HTML, replacing mermaid fences with inline SVG."""
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")

    # add_render_rule binds these to the renderer, so each takes `self` first.
    def fence(self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
        token = tokens[idx]
        if token.info.strip().lower() == "mermaid":
            svg = diagrams.get(token.content)
            if svg is None:  # pragma: no cover - _ensure_diagrams covers every block
                raise RuntimeError("a mermaid block reached rendering without an SVG")
            return f'<div class="diagram">{svg}</div>\n'
        return f"<pre><code>{_escape(token.content)}</code></pre>\n"

    def table_open(self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
        return '<div class="table-scroll"><table>'

    def table_close(self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
        return "</table></div>"

    md.add_render_rule("fence", fence)
    md.add_render_rule("table_open", table_open)
    md.add_render_rule("table_close", table_close)
    return str(md.render(text))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _title(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return f"{line[2:].strip()} — production-llm-platform"
    return "Architecture — production-llm-platform"


def _build(*, built: str, render: bool) -> str:
    source = _SOURCE.read_text(encoding="utf-8")
    diagrams = _ensure_diagrams(source, render=render)
    return _TEMPLATE.format(
        title=_escape(_title(source)),
        css=_CSS,
        banner=_GENERATED_BANNER,
        built=built,
        body=_render_markdown(source, diagrams),
    )


def _existing_build_stamp() -> str | None:
    """Recover the ``built`` stamp from the current HTML, if there is one.

    ``--check`` must compare *content*, not timestamps, or it would report drift
    on every run simply because the clock moved.
    """
    if not _OUTPUT.is_file():
        return None
    for line in _OUTPUT.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("<code>scripts/build_architecture.py</code>. Built "):
            return (
                line.strip()
                .removeprefix("<code>scripts/build_architecture.py</code>. Built ")
                .removesuffix(".")
            )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify architecture.html matches docs/architecture.md; do not write",
    )
    args = parser.parse_args()

    if args.check:
        stamp = _existing_build_stamp()
        if stamp is None:
            print(f"architecture.html is missing or malformed at {_OUTPUT}", file=sys.stderr)
            print("Run: uv run python scripts/build_architecture.py", file=sys.stderr)
            return 1
        try:
            # render=False: --check must never shell out to Node. It runs in CI
            # and in the test suite, neither of which should need a JS toolchain
            # to notice that a markdown edit was not regenerated.
            rebuilt = _build(built=stamp, render=False)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        # Reuse the recorded stamp so only real content changes count as drift.
        if rebuilt != _OUTPUT.read_text(encoding="utf-8"):
            print("architecture.html is out of date with docs/architecture.md", file=sys.stderr)
            print("Run: uv run python scripts/build_architecture.py", file=sys.stderr)
            return 1
        print("architecture.html is up to date.")
        return 0

    built = datetime.now(UTC).strftime("%Y-%m-%d")
    html = _build(built=built, render=True)
    _OUTPUT.write_text(html, encoding="utf-8")
    for orphan in _prune_orphans(_SOURCE.read_text(encoding="utf-8")):
        print(f"  removed unused {orphan.relative_to(_REPO_ROOT)}")
    print(f"Wrote {_OUTPUT.relative_to(_REPO_ROOT)} from {_SOURCE.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
