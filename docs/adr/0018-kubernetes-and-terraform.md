# ADR 0018 — Kubernetes via Helm, dev-mode in-cluster datastores vs Terraform-managed, and a validate-not-apply AWS Terraform boundary

- **Status:** Accepted
- **Date:** 2026-07-21
- **Stage:** 7 (Kubernetes)
- **Builds on:** ADR 0001 (Docker/compose baseline, non-root image), ADR 0005
  (datastore pooling and the `/health` vs `/ready` split), ADR 0009/0011/0016
  (the "nothing spends money or needs a real credential in CI" line).

## Context

Stage 1–6 run the whole stack with Docker Compose. Stage 7's job is a real
deployment story: get the existing API image onto Kubernetes with probes wired
to the honest `/health` (liveness) and `/ready` (readiness) endpoints, and
describe the cloud infrastructure it would run on — without a cloud account, and
without loosening the project's standing rule that CI stays hermetic, free, and
key-free.

Three decisions had to be made together, because they define the same
deployment boundary from three angles.

## Decision

### 1. Helm chart, not raw manifests

The API is deployed by a Helm chart
(`infrastructure/kubernetes/helm/production-llm-platform/`), parameterized via
`values.yaml`, rather than a directory of static YAML.

The deployment has to vary along axes this project already cares about — replica
count, image tag, the config profile, whether in-cluster dev datastores are
present, resource sizing, whether tracing is on. Raw manifests force either
copy-paste per environment (which the engineering rules forbid) or an ad-hoc
overlay tool. Helm gives one templated source with typed values, a render step
(`helm template`) that is CI-checkable without a cluster, and `helm lint`. It is
the same instinct as the `CompletionEngine`/config seams elsewhere: one
parameterized thing, not N near-copies.

### 2. Dev-mode in-cluster datastores, gated and clearly labelled — never the production path

For a local `kind` cluster to reach `/ready → ok`, Postgres, Redis and Qdrant
have to exist somewhere reachable. The chart includes minimal Deployments +
Services for all three, **gated behind `devDependencies.enabled`** (default
`false`), using the exact images pinned in `docker-compose.yml`. They are
ephemeral (`emptyDir`, single replica, no backups, no auth hardening).

These exist **solely** to verify the chart end-to-end on `kind` without a cloud
account. **Production sets `devDependencies.enabled=false` and points
`DATABASE_URL`/`REDIS_URL`/`QDRANT_URL` at the managed services Terraform
provisions** (RDS/ElastiCache/Qdrant). The two are never conflated — the chart's
README and the templates' own comments say so, and the URLs live in the Secret
so no credential-bearing string leaks into a ConfigMap.

This mirrors the split the project already draws: compose's in-repo Postgres is
a dev convenience; the real one is external. The chart makes that explicit and
refuses to blur it.

### 3. Terraform targets AWS, and is validated but never applied

The Terraform modules (`infrastructure/terraform/`) target **AWS** explicitly —
a real provider must be named to write real code — with one module each for
networking (VPC), cluster (EKS), managed Postgres (RDS), managed Redis
(ElastiCache), object storage (S3), and secrets (Secrets Manager **references**,
no values), wired by a root module.

The CI-safe gate is `terraform init` (local backend, no remote state) +
`terraform validate` + `terraform fmt -check`. **`terraform plan` and `apply`
are never run**: they need real AWS credentials this project does not have. This
is the identical treatment the repo gives paid LLM APIs — nothing spends money or
touches a real external account without deliberate human action outside CI. It
is a scope boundary, stated as one, not an oversight to be "fixed" later.

Two secret-handling choices fall out of the same rule: the RDS master password
is RDS-managed in Secrets Manager (`manage_master_user_password`), never set in
Terraform; and the app API keys are declared as empty Secrets Manager containers
with no `secret_version`, so no secret value is ever committed to source or
written to plaintext state.

## Consequences

**Positive**

- One templated deployment source, CI-checkable two ways without a cluster
  (`helm lint`, `helm template`) and one way with (`kind` install).
- The dev/prod datastore boundary is explicit and enforced by a flag, not by
  hoping nobody points production at an `emptyDir` Postgres.
- Real, valid AWS IaC exists and is continuously format- and schema-checked in
  CI, at zero cloud cost and zero credential risk.
- The no-secret-values discipline extends unbroken from Python into both Helm
  and Terraform.

**Negative / accepted limitations**

- `helm lint` + `helm template` + `terraform validate` prove the manifests and
  config are *well-formed*, not that a real EKS apply succeeds — `validate` never
  contacts AWS, so a runtime-only error (a quota, an IAM edge case, an
  unsupported instance type in a region) would only surface at an `apply` this
  project never runs. Accepted: same class of gap as "hermetic tests can't catch
  a wrong belief about the real API" (ADR 0015).
- Sizing (instance classes, node counts, storage, single-AZ NAT, single-node
  Redis, no multi-AZ RDS) is illustrative. Right-sizing, HA and load-shaped
  capacity are **Stage 9's** job, not this one's.
- The `kind` verification uses placeholder API keys. The `prod` profile
  validates the keys are *present*, not valid; `/ready` never calls a provider,
  so the deployment reaches `Ready` locally, exactly as the CI Docker job already
  boots a prod container with dummy keys (ADR 0006/0011).
