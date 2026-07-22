# Stage 7 — Kubernetes

## Objective

Deploy the existing API image to a real local Kubernetes cluster via a Helm
chart, with liveness/readiness probes wired to the existing `/health` and
`/ready` endpoints. Write Terraform modules for cloud provisioning —
validated, never applied (no cloud account exists for this project).

## Decided before this stage (do not re-litigate)

- **Helm chart, not raw manifests.** Parameterized via `values.yaml`, under
  `infrastructure/kubernetes/helm/production-llm-platform/`.
- **Local verification via `kind`** (Kubernetes-in-Docker), not minikube.
  This stage's "container boot is required" equivalent: actually
  `helm install` into a real `kind` cluster and confirm pods go `Ready` — not
  just lint the YAML and call it done.
  **Safety check first:** the machine may or may not have Docker Desktop's
  own built-in Kubernetes enabled — unknown going in, do not assume either
  way. Before creating anything, run `kubectl config get-contexts` and note
  what's there. `kind` runs independently of Docker Desktop's Kubernetes
  toggle (it uses the Docker engine directly, not that feature), but both
  can register `kubectl` contexts, so after `kind create cluster` explicitly
  confirm the current context is the `kind-*` one (`kubectl config
  current-context`) before running `helm install` — never deploy to
  whatever context happens to be default without checking. If Docker
  Desktop's Kubernetes turns out to already be enabled, leave it alone and
  untouched; use the `kind` cluster for everything in this stage regardless.
- **Datastores in the chart are dev-only, clearly labeled.** For a `kind`
  cluster to actually reach `/ready` → `ok`, Postgres/Redis/Qdrant need to
  exist somewhere reachable. Include minimal in-cluster Deployments for all
  three, gated behind a `devDependencies.enabled` values flag, with a comment
  and a README section stating explicitly: **production points
  `DATABASE_URL`/`REDIS_URL`/`QDRANT_URL` at the managed services Terraform
  provisions below — these in-chart datastores exist only so `kind` can
  verify the deployment end-to-end locally.** Do not conflate the two.
- **Terraform target: AWS**, stated explicitly (a real provider must be
  named to write real code against). Modules: cluster (EKS), managed
  Postgres (RDS), managed Redis (ElastiCache), object storage (S3),
  networking (VPC), secrets (Secrets Manager references, not values).
- **Terraform is validated, never applied.** `terraform init` (local
  backend, no real state storage) + `terraform validate` + `terraform fmt
  -check` must all pass and are the CI-safe gate. `terraform plan` against
  real AWS needs real credentials this project doesn't have — do not attempt
  it, do not fake credentials, state this explicitly as a deliberate
  boundary in the self-report, same treatment as this project already gives
  paid LLM APIs.

Write one ADR (0018, continuing sequentially) covering: Helm-vs-raw,
dev-mode in-cluster datastores vs Terraform-managed split, and the
AWS-target/validate-not-apply Terraform boundary.

## Scope

1. **Helm chart** (`infrastructure/kubernetes/helm/production-llm-platform/`):
   `Chart.yaml`, `values.yaml`, `templates/` with `Deployment`, `Service`,
   `ConfigMap` (non-secret settings), a `Secret` template (values injected at
   install time via `--set`/`--set-file`, never committed with real values),
   `livenessProbe` → `/health`, `readinessProbe` → `/ready`, resource
   requests/limits, configurable replica count. `HorizontalPodAutoscaler` is
   a nice-to-have, not required.

2. **Dev-mode datastore templates**, gated behind
   `devDependencies.enabled: true` in `values.yaml` — minimal Deployments +
   Services for Postgres, Redis, Qdrant using the same images already pinned
   in `docker-compose.yml`. Document in the chart's own README exactly why
   these exist and that they are not the production path.

3. **Terraform modules** (`infrastructure/terraform/`): one module each for
   cluster (EKS), managed Postgres (RDS), managed Redis (ElastiCache),
   object storage (S3), networking (VPC), and a secrets module referencing
   AWS Secrets Manager (no real secret values). A root module wiring them
   together. State the AWS region/sizing choices are illustrative, not
   sized for real load (that's Stage 9's job).

4. **CI wiring** — two new required jobs, both hermetic:
   - `helm lint` + `helm template` (render check) — no cluster needed.
   - `terraform fmt -check` + `terraform validate` (local backend) — no AWS
     credentials needed.
   Consider (not required, but this project has held this bar before) a
   third CI job that spins up `kind`, `helm install`s the chart with
   `devDependencies.enabled`, and curls `/ready` inside the runner — matches
   the existing CI job that already boots live datastore containers for the
   Docker build check. If you add it, state the exact steps; if you decide
   against it, state why in the self-report rather than silently omitting.

5. **Stub folder cleanup**: `infrastructure/kubernetes/README.md` and
   `infrastructure/terraform/README.md` get updated from "planned, not yet
   implemented" to reflect what's actually built.

## Required conventions (do not deviate)

- One ADR (0018, continuing sequentially) — the combined decision from
  above.
- `docs/PROJECT_STATUS.md` updated at end of stage — mark stage 7 complete,
  remove the stage-7 row from any "deferred" language, leave other stages'
  rows untouched.
- `CLAUDE.md` stays under 150 lines. State before/after line count. Update
  the "Deferred" table — Stage 7's row (`K8s manifests, Terraform`) is done,
  remove it.
- `docs/architecture.md` updated to reflect the deployment topology
  (Helm-deployed API, dev-mode vs Terraform-managed datastores); regenerate
  via `uv run python scripts/build_architecture.py` and state the exact
  command. Never hand-edit `architecture.html`.
- `pyproject.toml` / `uv.lock` unaffected — this stage adds no Python
  dependencies.
- No hardcoded secrets anywhere, including in Helm values or Terraform
  `.tfvars` — a test/grep already enforces this for Python; extend the same
  discipline here.
- Stub folder for `security` untouched this stage.
- **Real local deployment is required before self-report, not deferrable —
  this stage's equivalent of the container-boot rule.** Stand up a `kind`
  cluster, `helm install` with `devDependencies.enabled=true`, confirm every
  pod reaches `Ready`, port-forward the API service, and curl `/health` and
  `/ready` — `/ready` must report all three stores `ok`. State the exact
  commands and their actual output. A chart that only passes `helm lint`
  does not satisfy this stage.

## Testing requirements

- `helm lint` clean.
- `helm template` renders without error and produces valid-looking manifests
  (spot-check the probe paths and ports are correct in the rendered output).
- `terraform fmt -check` and `terraform validate` clean for every module.
- The full existing Python quality gate (`ruff`, `format`, `mypy`, `pytest`)
  still passes unchanged — this stage shouldn't touch Python source, confirm
  test count is unchanged from Stage 6's 311.
- The real `kind` deployment from the section above, with actual terminal
  output captured, not paraphrased.

## Explicit constraints

- Do not commit or push. Build, test, self-report only — commit happens only
  after independent manual verification, as a separate step.
- Do not touch the `security` stub folder.
- Use the canonical stage name `stage-07-kubernetes` verbatim everywhere.
- Do not run `terraform apply` or `terraform plan` against real AWS under
  any circumstances this stage — no credentials exist, and this is a
  deliberate scope boundary, not an oversight to fix.
- Tear down the `kind` cluster when done unless you have a reason to leave
  it running (state which, either way, in the self-report).

## Self-report requirements

Write `docs/stage-summaries/stage-07-kubernetes.md` containing:

- What was implemented, file by file
- `CLAUDE.md` line count before/after, and confirmation the Stage 7
  deferred-table row was removed
- The exact `kind`/`helm install` commands run and their actual output,
  including pod status and the `/health`/`/ready` curl results
- Confirmation the Python quality gate is unchanged (test count, ruff/mypy/
  format results)
- Confirmation `helm lint`/`helm template` and `terraform fmt -check`/
  `terraform validate` all pass, with actual output
- Whether a `kind`-based CI job was added or deliberately not — and why
- Confirmation `docs/architecture.md` was updated and
  `scripts/build_architecture.py` was re-run, with the exact command
- Any deviations from this scope and why
- Known limitations or deliberately deferred items (e.g. Terraform was
  never applied — restate why, even though it's already agreed)

State only what was directly run and observed this stage. Do not restate
prior-stage claims as if re-verified.
