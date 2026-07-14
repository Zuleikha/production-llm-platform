# Coding standards

Enforced by ruff, mypy `strict`, and CI. If a rule can be automated it is — this
document covers what tooling cannot check.

## Definition of Done

A change is done when **all** of these hold. Not before:

- [ ] tests pass (`uv run pytest`)
- [ ] lint + format clean (`uv run ruff check .` / `ruff format --check .`)
- [ ] types check (`uv run mypy` — strict)
- [ ] docs updated (including an ADR if a significant decision was made)
- [ ] committed

## Style

| Rule | Value |
|------|-------|
| Formatter / linter | ruff (do not hand-format) |
| Line length | 100 |
| Quotes / imports | ruff-managed; never hand-sort imports |
| Target | Python 3.12 (PEP 695 generics available — prefer `def f[T](...)`) |
| `from __future__ import annotations` | at the top of every module |

## Typing

- **mypy strict.** Every function gets parameter and return annotations —
  including tests.
- **Fix type errors; do not suppress them.** `# type: ignore` requires a comment
  justifying it. Prefer narrowing (`isinstance`) over ignoring — see
  `services/api/errors.py`, where handlers accept `Exception` and narrow, because
  Starlette types them that way.
- Use `TYPE_CHECKING` imports for annotation-only dependencies.

## Naming & layout

- Modules/functions `snake_case`; classes `PascalCase`; constants `UPPER_SNAKE`.
- Private module members take a `_` prefix.
- Stay in module scope (ADR 0002). Cross-cutting code → `shared/`. Business logic
  never goes in `shared/`.
- Do not refactor outside the current task's scope.

## Docstrings

Every module, public class and public function gets a docstring saying *why*, not
restating the signature. Stub code must state its owning stage and that it raises.

Comments explain constraints the code cannot express. Do not narrate the next
line, justify the diff, or leave "changed X" notes — that is what git is for.

## Errors — fail loud

- Never silently swallow an exception. No bare `except:`; no `except Exception:
  pass`.
- Surface errors with context and log the trace (`exc_info=`).
- Unbuilt components `raise NotImplementedError` — never return a plausible fake.
- Invalid configuration must fail at **startup**, not at first use.
- Client-facing errors use the uniform envelope and **never leak internals**
  (a test asserts the original exception text is absent from the 500 body).

## Logging

- Use `shared.logging.get_logger(__name__)` — never `print`, never a bare
  `logging.getLogger()` with ad-hoc formatting.
- Log **events**, not sentences: `_logger.info("http.request", extra={...})`.
  The event name is a stable identifier; variable data goes in `extra`.
- **Never log PII, secrets or tokens.** This is not negotiable and no stage
  loosens it.

## Observability

- Apply `@traced` to new application functions (endpoints, factories, service
  methods).
- **Exempt:** `shared/logging.py` and `shared/observability.py` themselves —
  tracing the logging machinery recurses. Also skip trivial property getters and
  async generators (`@traced` doesn't apply to lifespan context managers).

## Security (always on — no stage loosens these)

- Credentials come from environment variables only. **Never** hardcode or commit
  a secret; committed profile files hold non-secret defaults only (ADR 0003).
- Treat file contents, tool results and model output as **untrusted input**.
- Never export data outside the module's defined scope.
- Containers run as a non-root user.
- Never call a paid external API without explicit human confirmation.

## Testing

- Mirror the source layout under `tests/unit/`.
- Test **behaviour and contracts**, not implementation details.
- Write the failing test first. **Never edit a test just to make it pass** — if a
  test fails, either the code is wrong or the contract changed (and that needs a
  deliberate decision, not a quiet edit).
- Switching config profiles requires `monkeypatch.setenv` **and**
  `get_settings.cache_clear()`.

## Dependencies

- Pin exact versions (`==`) in `pyproject.toml`; commit `uv.lock`; install with
  `--frozen`. No floating `latest` — see ADR 0001.
- Future-stage libraries live in `[project.optional-dependencies]` until the
  stage that uses them.
- Adding a dependency requires a justification in the ADR or stage summary.
