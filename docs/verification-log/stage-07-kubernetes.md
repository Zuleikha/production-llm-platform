# Stage 7 — Kubernetes — Verification Log

Independent verification, separate from Claude Code's self-report at
`docs/stage-summaries/stage-07-kubernetes.md`. Performed by zulu, checked
against the actual source/diff and re-run tooling, not against the summary's
claims alone.

**Date:** 2026-07-22
**Stage:** 7 — Kubernetes
**Base commit (unchanged, pre-stage-7):** 067512f — stage-7 work is
**uncommitted** at the time of this log; verification was performed against
the working tree, before any commit, per this project's own PER-STAGE PROCESS
("commit happens only after independent manual verification").

## Result: PASS

All checklist items verified. One real defect was found and fixed during
verification — pre-existing CRLF line-ending pollution across 22 tracked
files, unrelated to CC's stage-7 work itself. See "Discrepancy found and
resolved" below. Nothing in CC's self-report was found to be inaccurate.

## Environment / tooling

| Check | Result |
|---|---|
| `uv sync` | Clean. Resolved 141 packages, checked 139 — venv already in place. |
| `uv run pytest -q` | Terminal summary line did not print (see discrepancy below); confirmed via `--junit-xml` instead: `tests="320" skipped="9" failures="0" errors="0"` → 311 passed. Matches CC's claimed 311/9 exactly. |
| `uv run ruff check .` | All checks passed. |
| `uv run ruff format --check .` | 73 files already formatted. |
| `uv run mypy .` | Success: no issues found in 74 source files. |
| `wc -l CLAUDE.md` | 149 lines — within the 150-line limit. Matches CC's claimed "149 → 149." |
| `helm lint` | `1 chart(s) linted, 0 chart(s) failed`. One cosmetic `[INFO]` (missing chart icon), not a defect. |
| `helm template` (rendered output) | Confirmed real manifests, not just template source: API pod → `containerPort: 8000`, `livenessProbe.path: /health`, `readinessProbe.path: /ready`, `component: api`. Dev-datastore pods → distinct `component: dev-postgres` / `dev-redis` / `dev-qdrant` labels, confirming the selector-scoping fix actually works in rendered output. |
| `terraform fmt -check -recursive` | Clean, exit code 0. |
| `terraform init -backend=false` | Successful — all 6 modules (cluster, networking, postgres, redis, secrets, storage) initialized, AWS provider v5.100.0 installed. |
| `terraform validate` | `Success! The configuration is valid.` |

## Discrepancy found and resolved

**Pre-existing CRLF line-ending pollution across 22 tracked files**, unrelated
to stage 7. `git status` showed ~19-30 modified files touching
`services/`, `tests/`, and several docs — surprising, since stage-07-kubernetes.md
explicitly states this stage should not touch Python source. Investigated with
a CR-stripped content diff (`git show HEAD:<file> | tr -d '\r'` vs the working
tree, per file) rather than trusting `git diff --stat` alone, since a file with
an unchanged line count can show misleadingly-equal insertion/deletion counts
even when real content changed underneath a full line-ending flip.

Root cause: working-tree files had CRLF terminators; the committed blobs are
LF. `core.autocrlf` was unset. This predates tonight's session (unclear
exact origin — likely an editor or tool on this Windows machine) and is not
something CC introduced this stage.

Split cleanly into two groups:
- **22 files: pure CRLF noise, zero real content difference.** Confirmed via
  the CR-stripped diff showing identical content both sides.
- **3 files (`.gitignore`, `CLAUDE.md`, `architecture.html`): real content
  changes *and* CRLF, mixed together.** Real content confirmed intact after
  the fix.
- **9 files with real stage-7 content changes and no CRLF issue at all**
  (`.github/workflows/ci.yml`, `docs/PROJECT_STATUS.md`, `docs/adr/README.md`,
  `docs/architecture.md`, both infra READMEs, plus the 3 mixed files above) —
  confirmed clean, legitimate stage-7 edits.

Fix: added `.gitattributes` (`* text=auto eol=lf`) so git normalizes line
endings for comparison purposes regardless of working-tree state — this alone
resolved the 22 pure-noise files without needing to touch their content.
Verified via `git diff --stat` showing zero diff on all 22 afterward. A
standing instruction was also added to `docs/architecture-history/README.md`
so this repo's separate personal-gallery tool stays in sync with future stages
without needing to be asked each time (that folder remains gitignored, never
committed).

**Secondary issue, found and fixed: `.git/index.lock` reappeared repeatedly**
during verification (at least 3 times), each accompanied by an "Operation not
permitted" unlink failure on the lock file and, once, on an orphaned
`.git/objects/34/tmp_obj_*` file. Root-caused by CC on request: every time git
wrote a new file into `.git/` (a loose object, or `index.lock`), Windows
Defender's real-time scanning opened a handle on the fresh file; git's
near-immediate rename/unlink of that same file then hit a sharing violation
while Defender still held it, which git reports as "Operation not permitted."
Confirmed not caused by OneDrive (repo is on `D:`, outside OneDrive's synced
`C:\Users\New User\OneDrive`; `OneDrive.exe` wasn't even running) or any
lingering git/editor process (none found running).

Fix: added a Defender real-time-scanning exclusion scoped to
`D:\GIT-REPOS\production-llm-platform` only (`Add-MpPreference
-ExclusionPath`) — Defender stays fully enabled everywhere else. Removed the
stranded lock and orphaned temp object once confirmed no git process was
running. Verified genuinely fixed, not just quiet: `git status`, five
consecutive `git add -A --dry-run` runs, a real `git add -A`, and
`git fsck --full` all came back clean with no new lock or orphaned object.
One side effect of the fix landing mid-session: the index write that had
failed silently before also meant `.gitattributes`'s earlier successful
`git add` had not durably persisted — it reverted to untracked and had to be
re-staged once the underlying issue was actually resolved. Confirmed staged
and stable on the re-check that followed.

## Static / structural checks

| Check | Result |
|---|---|
| Secret grep (new Helm/Terraform files) | One hit: `values.yaml:150` `password: platform`. Inspected in context — explicitly commented as a dev-only placeholder identical to `docker-compose.yml`'s existing dev defaults, gated behind `devDependencies.enabled: false` (opt-in only), for an ephemeral in-cluster datastore never exposed outside the cluster. Consistent with existing project precedent, not a new violation. The other two grep hits (`manage_master_user_password` in the Terraform postgres module) were false positives — that setting is the *correct* pattern (AWS-managed password in Secrets Manager, no value in code). |
| Stub check — `services/security/` | Untouched. All three files (`README.md`, `__init__.py`, `base.py`) still carry Jul 14 mtimes; `git log -1 -- services/security/` confirms the last commit touching the folder is the Stage 1 foundation commit. |
| Folder structure vs stage-07-kubernetes.md spec | Helm chart has all required templates (`Chart.yaml`, `values.yaml`, `Deployment`, `Service`, `ConfigMap`, `Secret`, plus `HorizontalPodAutoscaler` as the optional nice-to-have) and the gated dev-datastore templates. Terraform has exactly the 6 required modules (cluster/networking/postgres/redis/secrets/storage) plus a root module. Matches spec sections 1-3 exactly. |
| `deployment.yaml` source review | `wait-for-datastores` initContainer present, gated behind `.Values.devDependencies.enabled`, with an inline comment explaining the exact race it closes (k8s starts pods in parallel with no `depends_on`-equivalent; the app connects once at boot and never retries). Selector `matchLabels` includes `app.kubernetes.io/component: api`, confirming the second bug fix CC claimed. |
| `docs/architecture.md` vs actual code | Reviewed the real diff (`git diff HEAD -- docs/architecture.md`), not just the automated drift test. New "Deployment topology" section accurately describes the Helm chart structure, the prod/dev datastore split via `devDependencies.enabled`, the initContainer race-fix, and all 6 Terraform modules with the correct "validated, never applied" boundary. Stale "Empty placeholders" row and "no Kubernetes" non-goal language were correctly removed. `architecture.html` confirmed regenerated (real diff present, consistent with running through `scripts/build_architecture.py` rather than hand-edited). |
| `CLAUDE.md` diff | "Current state" header correctly reads "Stage 7 of 10 (Kubernetes), COMPLETE." Deferred table's stage-7 row removed; stage 8/9 rows untouched. |

## Not independently re-verified live tonight

The actual `kind` cluster deployment (pods reaching `Ready`, live
`/health`/`/ready` curls against a running cluster) was **not** re-run as
part of this verification pass — the cluster had already been torn down by
the time manual verification started. This relies on CC's own captured
terminal output from the build session (documented in
`docs/stage-summaries/stage-07-kubernetes.md`), which is consistent with
everything independently confirmed here via static analysis (`helm template`
rendered output, source review). If a fully independent live re-check is
wanted, it would require standing up `kind` again — deferred as a judgment
call given the static verification already matches on every point it can
check.

## Non-blocking notes (carried forward, not fixed this stage)

- `StarletteDeprecationWarning` (httpx/TestClient) and the Windows
  `.pytest_cache` "Access is denied" warning — both already documented in
  CLAUDE.md's Known issues, reconfirmed present, not investigated further.
- `pytest -q`'s final one-line summary (`N passed, M skipped in Xs`) did not
  print to the terminal in this Git Bash environment, even when redirected to
  a plain file — worth a look if it recurs and starts hiding real failures,
  but `--junit-xml` is a reliable workaround and gives an exact machine-readable
  count regardless.
## Sign-off

Stage 7 is verified complete. All functional, behavioral (where re-checkable),
and structural claims in CC's self-report hold up against independent
re-execution of the test/lint/type-check gate, static rendering of the Helm
chart, and Terraform validation. Two real, pre-existing environment defects
were found and fixed during verification — CRLF line-ending pollution (fixed
via `.gitattributes`) and a Windows-Defender-caused recurring `.git/index.lock`
(fixed via a scoped Defender exclusion, root-caused and proven by CC, not just
patched over). Nothing here blocks committing the stage-7 work.
