"""Shared markdown-to-HTML rendering for the repo's generated doc pages.

One styling source for every generated page — ``architecture.html``,
``case-study.html`` and ``demo.html`` — so they stay visually consistent and can
never drift apart stylistically. Factored out of ``build_architecture.py`` in
Stage 10; that script keeps its Mermaid-specific logic (diagram hashing,
``docs/diagrams/``, ``--render``) and calls in here for the CSS, the markdown
render and the page shell.

Same ADR 0010 contract as before: **no CDN, no JavaScript, pre-rendered, works
offline.** This module shells out to nothing and imports nothing beyond
``markdown-it-py`` — building any page needs only Python.
"""

from __future__ import annotations

from collections.abc import Callable

from markdown_it import MarkdownIt

_GENERATED_BANNER = "GENERATED FILE - DO NOT EDIT"

# The single source of styling for every generated page (GitHub-flavoured light +
# dark). Diagram-specific rules live here too so an architecture page and a prose
# page render identically where they overlap; a prose page simply uses no
# ``.diagram`` blocks.
CSS = """
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
<!-- {banner}. Source: {source_rel}
     Regenerate: {regenerate}
     {head_note} No CDN, no JS. -->
<style>{css}</style>
</head>
<body>
<div class="build-stamp">
  <strong>Generated file — do not edit.</strong>
  Rendered from <code>{source_rel}</code> by
  <code>{script_rel}</code>. Built {built}.
</div>
<main>
{body}
</main>
</body>
</html>
"""

# The literal that ``recover_build_stamp`` keys on. Kept as a format so both the
# template and the recovery read the same shape.
_STAMP_PREFIX = "<code>{script_rel}</code>. Built "


def escape(text: str) -> str:
    """HTML-escape the three characters that matter for inlining text safely."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def page_title(text: str, *, default: str) -> str:
    """The document's H1 as a ``<title>``, suffixed with the project name."""
    for line in text.splitlines():
        if line.startswith("# "):
            return f"{line[2:].strip()} — production-llm-platform"
    return default


def markdown_renderer(*, fence: Callable[..., str] | None = None) -> MarkdownIt:
    """A ``MarkdownIt`` configured the way every generated page renders markdown.

    Tables are wrapped in a horizontally-scrolling container (so the page body
    never scrolls sideways). Fenced code blocks render as ``<pre><code>`` unless a
    caller supplies its own ``fence`` rule — ``build_architecture.py`` passes one
    that turns ```` ```mermaid ```` fences into inlined SVG.
    """
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")

    def default_fence(self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
        return f"<pre><code>{escape(tokens[idx].content)}</code></pre>\n"

    def table_open(self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
        return '<div class="table-scroll"><table>'

    def table_close(self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
        return "</table></div>"

    md.add_render_rule("fence", fence if fence is not None else default_fence)
    md.add_render_rule("table_open", table_open)
    md.add_render_rule("table_close", table_close)
    return md


def render_markdown(text: str, *, fence: Callable[..., str] | None = None) -> str:
    """Render markdown ``text`` to an HTML fragment (body only, no page shell)."""
    return str(markdown_renderer(fence=fence).render(text))


def render_page(
    *,
    source_text: str,
    body_html: str,
    built: str,
    source_rel: str,
    script_rel: str,
    regenerate: str,
    head_note: str,
    title_default: str,
) -> str:
    """Wrap a rendered body in the shared HTML shell (head, CSS, build stamp)."""
    return _TEMPLATE.format(
        title=escape(page_title(source_text, default=title_default)),
        css=CSS,
        banner=_GENERATED_BANNER,
        built=built,
        body=body_html,
        source_rel=source_rel,
        script_rel=script_rel,
        regenerate=regenerate,
        head_note=head_note,
    )


def recover_build_stamp(html: str, *, script_rel: str) -> str | None:
    """Recover the ``built`` date stamp from an already-generated page.

    ``--check`` must compare *content*, not timestamps, or it would report drift on
    every run just because the clock moved. Returns ``None`` when the page has no
    recognisable stamp (missing or malformed).
    """
    needle = _STAMP_PREFIX.format(script_rel=script_rel)
    for line in html.splitlines():
        stripped = line.strip()
        if stripped.startswith(needle):
            return stripped.removeprefix(needle).removesuffix(".")
    return None


# Re-exported so callers that only need a value do not each re-declare it.
GENERATED_BANNER = _GENERATED_BANNER
