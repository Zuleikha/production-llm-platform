# Handoff: production-llm-platform, starting Stage 10 (Portfolio)

Repo: D:\GIT-REPOS\production-llm-platform (github.com/Zuleikha/production-llm-platform)
Target: ML/AI Engineer / MLOps Engineer roles — general production ML platform
engineering rigor, not domain-specific storytelling.

## Where things actually stand

9 of 10 stages complete and independently verified (not just self-reported):
Foundation, API, Agents, RAG, Observability, MLOps, Kubernetes, Security,
Reliability. Canonical status lives in docs/PROJECT_STATUS.md — check it
directly, don't trust a stale summary of it.

Stage 10 (Portfolio) is next: final polish, docs, demos, case study writeup.
Its scope has not been drafted into a stage prompt yet — that's the first
thing to do here, same process as every prior stage.

**Scope boundary, restated:** this project is dedicated solely to this build.
No CV, job search, or unrelated topics in this conversation thread — Stage 10
is about polishing and presenting *this repo*, not writing application
materials elsewhere.

## Before any Stage 10 work starts — loose ends from Stage 9

1. **Confirm CI is actually green on the pushed Stage 9 commit** — don't
   assume it from a clean local gate. If this hasn't been checked yet, check
   it before treating Stage 9 as closed.
2. **A malformed `ANTHROPIC_API_KEY` in `.env`.** Found during Stage 9
   verification: the key reads `ssk-ant-api03-...` — a doubled leading
   character, not a valid key shape (`sk-ant-api03-...`). This is separate
   from Stage 9's code and was never fixed (out of scope for a code
   verification pass). It blocks any real Anthropic call against the `dev`
   profile until re-pasted/rotated at the Anthropic console. Fix this before
   any Stage 10 work that needs a real model call (demos, screenshots, a
   real end-to-end walkthrough).
3. **`docs/architecture-history/generate.py` needs a re-run after Stage 9's
   completion commit landed.** During the build session it snapshotted the
   working-tree `docs/architecture.md` (no commit existed yet) and is
   documented to self-heal to the real git blob on the next run — but that
   next run hasn't happened. Run `uv run python docs/architecture-history/generate.py`
   once, confirm Stage 9's tab now reads from the commit, not the
   working-tree fallback. Gitignored, never committed.
4. **Open item, not a defect: `tests/load/locustfile.py` still can't produce
   real pool-tuning data.** Every simulated user authenticates as the same
   single `test-principal` (collides on one rate-limit bucket almost
   immediately) and the chat payload has no `conversation_id` and never
   triggers `document_search`, so Postgres/Qdrant pools are never actually
   exercised under load. Decide explicitly whether Stage 10 fixes this
   (multiple minted principals + a conversation_id + an occasional
   tool-triggering payload) or formally accepts it as a permanently deferred
   limitation in the final portfolio writeup — don't let it linger
   unaddressed by default.
5. **Minor, not urgent:** a `request_id: null` on the 500 that fires when
   Postgres is down mid-conversation (Scenario 1 of the chaos runbook) —
   likely pre-existing, never chased down. `pyyaml`, imported directly by
   `tests/unit/test_load_profile_compose.py`, isn't declared as an explicit
   dependency in `pyproject.toml` (present only transitively) — same class of
   risk the project already got burned by once with `sniffio`.

## Hard lessons from Stage 9's verification pass — worth knowing before touching Docker again

- **`docker compose`'s `COMPOSE_FILE` env var needs `;` as the separator on
  Windows, even inside Git Bash** — it's a native Windows binary parsing the
  variable with Windows rules, not the shell's rules. `:` fails silently with
  a "file not found" error that doesn't obviously point at the real cause.
- **`docker compose restart` does NOT re-read `.env`.** It restarts the
  existing container process with whatever environment it was *created*
  with. An `.env` edit needs `docker compose up -d --force-recreate <svc>` (or
  a full `up`) to actually take effect — a plain `restart` will silently keep
  serving the old values and make a fix look like it didn't work.
- **A `source .env` (or any `set -a; source ...`) in a shell contaminates
  every subsequent `docker compose` command run in that same shell**, because
  shell-exported env vars take precedence over both `.env` file contents and
  even profile-specific env files for docker-compose interpolation. If a
  `docker compose` command behaves like it's ignoring a `.env` edit, check
  `env | grep -c '^VARNAME='` in the current shell before assuming the file
  is wrong.
- **Root `.env`'s secrets can leak into a container regardless of the
  selected profile**, if `docker-compose.yml` interpolates them
  unconditionally (`${VAR:-}` pattern). Fixed for the `test` profile via a
  literal-value override file (`docker-compose.test.yml`) — the null/passthrough
  form and `--env-file` were both tried and empirically rejected. If a future
  stage adds a new profile-specific compose need, check this precedent first.

## The fixed per-stage process — do not deviate

1. Claude (a separate conversation) drafts stage prompt content for
   `docs/prompts/stage-10-portfolio.md`, based on `PROJECT_STATUS.md`'s scope
   for that stage. User reviews, pastes into the file, hands to Claude Code
   via the reusable prompt template (`docs/cc-stage-prompt-template.txt`).
2. CC builds, self-reports to `docs/stage-summaries/stage-10-portfolio.md`.
3. User pastes CC's self-report to Claude. Claude reviews critically — checks
   claims against evidence (read the actual diff, don't just trust the text),
   flags discrepancies before the user finds them.
4. User runs manual verification directly on the real machine. Stage 9 showed
   this is worth taking seriously even for a "polish" stage — check claims
   about docs/demos actually rendering, not just that files exist.
5. Once verification passes, Claude writes
   `docs/verification-log/stage-10-portfolio.md`.
6. Only then does CC commit — never before independent verification passes.
7. Confirm CI is actually green on the pushed commit before calling the stage
   (and the whole project) closed.

**All six close-out files, every stage, no exceptions** (per
`docs/contributing.md` → "Ending a stage"): stage summary, PROJECT_STATUS.md,
CLAUDE.md, architecture.md + regenerated architecture.html, README.md,
verification log. Stage 9 nearly shipped with README.md silently stale (still
said "Stage 8 of 10 complete") — it was only caught because someone checked it
directly before commit. Don't skip checking README.md again.

## Known environment quirks (carried forward, still true)

- Cross-drive uv / lock-install mismatch — fixed by `link-mode = "copy"` in
  `pyproject.toml`, do not remove.
- Never use the system Python at `D:\Python\Python312` — segfaults on
  `import ctypes`. Use `uv python install 3.12 && uv venv --managed-python --python 3.12`.
- Grafana port 3000 conflicts with `open-webui` on this machine — remapped to
  3001.
- `.git/index.lock` strands — two git actors racing the index (the CC harness's
  background `git status` poll + a standalone Git Bash window). One git actor
  at a time; a 0-byte lock with no `git.exe` running is safe to delete.

Ask for the current `docs/PROJECT_STATUS.md`, `CLAUDE.md`, and the Stage 9
verification log if you need to confirm anything above before proceeding —
don't assume repo access in a fresh conversation.
