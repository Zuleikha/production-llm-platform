# ADR 0010 — Architecture diagrams are pre-rendered inline SVG

- **Status:** Accepted
- **Date:** 2026-07-15
- **Stage:** 3 (Agents)
- **Supersedes:** the CDN-loaded Mermaid approach introduced with
  `scripts/build_architecture.py` in Stage 2.

## Context

`architecture.html` is generated from `docs/architecture.md` so the two cannot
drift. Stage 2 rendered its Mermaid diagrams **client-side from a pinned CDN**
(`cdn.jsdelivr.net/npm/mermaid@11.4.1`). The docstring at the time rejected
inlining the library as "~3MB of vendored JavaScript in every diff for a file
that is only ever read online".

That reasoning had a flaw, and Stage 2's own follow-up commit is the evidence:
the last thing done to the file was adding a fail-loud notice for **when the CDN
cannot be reached**. The page had a documented, expected failure mode in which
the diagrams — the entire reason the HTML view exists over the markdown — are
replaced by unreadable source text.

"Only ever read online" is also the wrong assumption. An architecture document is
read on a plane, in a locked-down corporate browser that blocks third-party
script origins, and during an incident when someone is trying to understand the
system. It should not need a network, and it should not hand a third-party origin
a request every time someone opens it.

## Decision

**Render every Mermaid diagram to SVG at build time and inline it. The page loads
nothing and runs no JavaScript.**

### The rendering toolchain is not required to build the page

This is the part that makes it viable, and it is the reason the "3MB of vendored
JS" objection does not apply here.

- Each diagram is rendered once to `docs/diagrams/<slug>-<hash>.svg`, keyed by a
  SHA-256 of its **Mermaid source** (newline-normalised, so a Windows checkout and
  a Linux CI runner agree).
- Those SVGs are committed.
- **Building the HTML only inlines them.** `--check` — which the test suite and
  CI run — needs nothing but Python. A test (`test_building_the_page_never_shells_out_to_node`)
  pins this by removing `npx` and making `subprocess.run` raise.
- Node + `mermaid-cli` are needed **only when a diagram's source actually
  changes**, on the machine of whoever changed it.

So the JS toolchain is a contributor-side tool, not a build or CI dependency. The
diff cost is the rendered SVGs (~360KB total across six diagrams), not a vendored
library — and they only change when a diagram does.

### Fail loud, both ways

- A diagram in the markdown with no rendered SVG **fails the build**, and fails
  `--check` with "run the build script". Drift between the diagrams and the prose
  is caught the same way drift between the HTML and the markdown already is.
- A `mermaid-cli` failure raises with its stderr. A silently skipped diagram
  would ship a page with a hole in it.
- Orphaned SVGs — diagrams removed from the markdown — are pruned on build and a
  test fails if any survive. A stale diagram is a picture of an architecture that
  no longer exists, which is worse than no picture.

### The renderer is pinned

`@mermaid-js/mermaid-cli@11.4.2`. An unpinned renderer would let the committed
diagrams change under us between builds — the same drift the committed SVGs exist
to prevent.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **Keep the CDN (status quo)** | The diagrams — the reason the HTML exists — vanish without a network, in a locked-down browser, or when the CDN eventually moves. A doc that only works online fails exactly when someone is diagnosing an outage. Also a third-party request per page view. |
| **Inline the Mermaid library (~3MB)** | Self-contained, but ships a JS engine to draw six static pictures, in every diff, forever. Pre-rendering gives the same independence for a fraction of the bytes and no runtime. |
| **Hand-author the SVGs** | No toolchain at all — genuinely tempting. But six diagrams become laborious to author and, worse, laborious to *keep accurate*: every future stage pays that tax, and the likely outcome is diagrams that quietly stop matching the system. Mermaid-in-markdown stays readable and diffable in the source of truth. |
| **HTML + CSS boxes** | No dependencies, but CSS cannot draw arrows properly. The component map, the sequence diagram and the state machine would all degrade badly — and those are the three that carry the most meaning. |
| **Render SVGs in CI instead of committing them** | Moves a headless-Chromium download into every CI run and makes CI a hard dependency for viewing the docs. Committing the output is what keeps `--check` Python-only. |
| **Key SVGs by filename/index rather than source hash** | An edited diagram would silently keep its old rendering. The hash is what makes staleness detectable. |
| **Leave `--check` able to shell out to Node** | CI and the test suite would need a JS toolchain to notice a markdown edit. The whole point is that they don't. |

## Consequences

**Positive**

- `architecture.html` renders identically offline, in any browser, with
  JavaScript disabled, forever. Verified: zero external URLs, zero `<script>`
  tags, six inline `<svg>` elements.
- No third-party origin is contacted when someone opens the docs.
- CI and the test suite need no JavaScript toolchain.
- Diagrams still live as Mermaid inside the markdown source of truth — readable
  and diffable where the prose is.
- Diagram staleness is now a build failure rather than a thing to notice.

**Negative / accepted trade-offs**

- **Changing a diagram needs Node + npx**, and the first render downloads
  `mermaid-cli` and a headless Chromium (~200MB, cached afterwards). This is the
  real cost. It falls only on whoever edits a diagram, and the error message says
  exactly what to install and why.
- **Rendered SVGs are committed artefacts** (~360KB across six). They churn the
  diff when a diagram changes. Accepted: they change rarely, and they are the
  reason the page needs nothing.
- **The rendered diagrams are light-themed**, so the page shows them on a white
  plate in dark mode rather than restyling them. Mermaid's dark theme would mean
  rendering and committing every diagram twice.
- **`mermaid-cli` is a heavyweight tool** (it drives a real browser). It is not in
  `pyproject.toml` and is not managed by `uv` — it is invoked via `npx` with a
  pinned version, which is a second, unmanaged toolchain in the repo.
- **Mermaid keywords are now a build-time trap.** A node id like `graph` or `end`
  fails the render (this bit during Stage 3 — `graph` is the legacy flowchart
  declaration). The failure is loud and the stderr names the line, but it is a
  sharp edge that did not exist when the browser rendered leniently at view time.
