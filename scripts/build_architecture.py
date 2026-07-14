"""Generate ``architecture.html`` from ``docs/architecture.md``.

The markdown is the single source of truth; this script renders a browsable view
of it with Mermaid diagrams drawn rather than shown as code. Two documents
describing one architecture would drift, so there is only ever one to edit.

    uv run python scripts/build_architecture.py           # write architecture.html
    uv run python scripts/build_architecture.py --check   # exit 1 if out of date

``--check`` is what ``tests/unit/test_architecture.py`` and CI use to fail when
someone edits the markdown and forgets to regenerate — the same guard the repo
already applies to ``version.py`` vs ``pyproject.toml``.

Mermaid is loaded from a pinned CDN bundle: inlining it would add ~3MB of
vendored JavaScript to every diff for a file that is only ever read online.
Without a network the prose still renders; only the diagrams stay as text.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from markdown_it import MarkdownIt

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SOURCE = _REPO_ROOT / "docs" / "architecture.md"
_OUTPUT = _REPO_ROOT / "architecture.html"

# Pinned: an unpinned CDN import would let the rendering change under us.
_MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs"

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
/* Diagrams keep their natural size (see mermaid useMaxWidth:false) and scroll
   inside this box, so the page body never scrolls sideways. */
.mermaid {
  margin: 1.5rem 0; padding: 1rem 0; overflow-x: auto;
  border: 1px solid var(--border); border-radius: 6px; background: var(--code-bg);
}
.mermaid svg { display: block; margin: 0 auto; height: auto; }
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
     Regenerate: uv run python scripts/build_architecture.py -->
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
<script type="module">
import mermaid from "{mermaid_cdn}";
const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
// useMaxWidth:false keeps diagrams at their natural size instead of shrinking
// them to the container, which renders the labels unreadably small. Wide
// diagrams scroll inside .mermaid (overflow-x:auto) rather than shrink.
mermaid.initialize({{
  startOnLoad: true,
  theme: dark ? "dark" : "default",
  securityLevel: "strict",
  flowchart: {{ useMaxWidth: false, htmlLabels: true, curve: "basis" }},
  sequence: {{ useMaxWidth: false }},
  state: {{ useMaxWidth: false }},
}});
</script>
</body>
</html>
"""


def _render_markdown(text: str) -> str:
    """Convert markdown to HTML, turning mermaid fences into mermaid blocks."""
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")

    # add_render_rule binds these to the renderer, so each takes `self` first.
    def fence(self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
        token = tokens[idx]
        if token.info.strip().lower() == "mermaid":
            # Mermaid needs its source verbatim; the library renders it client-side.
            return f'<pre class="mermaid">{_escape(token.content)}</pre>\n'
        return f"<pre><code>{_escape(token.content)}</code></pre>\n"

    def table_open(self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
        return '<div class="table-scroll"><table>'

    def table_close(self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
        return "</table></div>"

    md.add_render_rule("fence", fence)
    md.add_render_rule("table_open", table_open)
    md.add_render_rule("table_close", table_close)
    return md.render(text)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _title(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return f"{line[2:].strip()} — production-llm-platform"
    return "Architecture — production-llm-platform"


def _build(*, built: str) -> str:
    source = _SOURCE.read_text(encoding="utf-8")
    return _TEMPLATE.format(
        title=_escape(_title(source)),
        css=_CSS,
        banner=_GENERATED_BANNER,
        built=built,
        body=_render_markdown(source),
        mermaid_cdn=_MERMAID_CDN,
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
        # Reuse the recorded stamp so only real content changes count as drift.
        if _build(built=stamp) != _OUTPUT.read_text(encoding="utf-8"):
            print("architecture.html is out of date with docs/architecture.md", file=sys.stderr)
            print("Run: uv run python scripts/build_architecture.py", file=sys.stderr)
            return 1
        print("architecture.html is up to date.")
        return 0

    built = datetime.now(UTC).strftime("%Y-%m-%d")
    _OUTPUT.write_text(_build(built=built), encoding="utf-8")
    print(f"Wrote {_OUTPUT.relative_to(_REPO_ROOT)} from {_SOURCE.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
