# Stage 07 — Kubernetes (self-report)

**Status:** complete. **Version:** `0.1.0` (unchanged — no Python source touched).
**Date:** 2026-07-21.

This is Claude Code's self-report: only what was directly run and observed this
stage. Independent verification is logged separately at
`docs/verification-log/stage-07-kubernetes.md`.

---

## 1. What was implemented, file by file

### Helm chart — `infrastructure/kubernetes/helm/production-llm-platform/`

| File | What it does |
|------|--------------|
| `Chart.yaml` | chart metadata; `version` 0.1.0, `appVersion` 0.1.0 (lockstep with the image tag) |
| `values.yaml` | all defaults; `ENVIRONMENT: prod` default; blank secrets/URLs; `devDependencies.enabled: false`; resources, probes, HPA, security context |
| `templates/_helpers.tpl` | name/label helpers + three helpers that resolve `DATABASE_URL`/`REDIS_URL`/`QDRANT_URL` (explicit override → dev-service host → empty) |
| `templates/deployment.yaml` | API `Deployment`: `envFrom` ConfigMap+Secret, `livenessProbe → /health`, `readinessProbe → /ready`, resources, non-root security context, config/secret checksum annotations, and a `wait-for-datastores` initContainer (dev mode only) |
| `templates/service.yaml` | `ClusterIP` on 8000, selector scoped to `component: api` |
| `templates/configmap.yaml` | non-secret settings only (`ENVIRONMENT`, `LOG_*`, `API_PORT`, optional OTLP endpoint) |
| `templates/secret.yaml` | datastore URLs + API keys; each key omitted when blank so nothing empty/fake is baked in |
| `templates/hpa.yaml` | optional `HorizontalPodAutoscaler` (gated by `autoscaling.enabled`) |
| `templates/dev-datastores.yaml` | **dev-only** Postgres/Redis/Qdrant Deployments+Services, gated by `devDependencies.enabled`, same images as `docker-compose.yml`, ephemeral `emptyDir` |
| `templates/NOTES.txt` | post-install rollout/port-forward hints |
| `.helmignore`, `README.md` | packaging ignores; chart README documenting the dev-vs-managed datastore split |

### Terraform — `infrastructure/terraform/` (target: AWS)

Root: `versions.tf` (aws `~> 5.40`, local backend), `providers.tf`,
`variables.tf` (illustrative region/sizing defaults), `main.tf` (wires six
modules), `outputs.tf`, `terraform.tfvars.example` (no secrets), `.gitignore`.

Modules (`modules/<name>/` each with `main.tf`/`variables.tf`/`outputs.tf`/`versions.tf`):
`networking` (VPC, public/private subnets, IGW, NAT, route tables), `cluster`
(EKS + managed node group + IAM roles), `postgres` (RDS; **`manage_master_user_password`**
so no password is ever in TF/state), `redis` (ElastiCache replication group),
`storage` (private/versioned/encrypted S3), `secrets` (Secrets Manager secret
**containers only — no `secret_version`, no values**).

### ADR + docs + CI

- `docs/adr/0018-kubernetes-and-terraform.md` — the combined decision
  (Helm-vs-raw · dev-mode vs Terraform-managed datastores · AWS validate-not-apply);
  indexed in `docs/adr/README.md`.
- `docs/architecture.md` — new "Deployment topology (Kubernetes / Helm, Stage 7)"
  section; Stage-7 row removed from the "planned" table; header bumped to Stage 7.
- `docs/PROJECT_STATUS.md` — Stage 7 marked complete (7/10, 70%); next milestone
  Stage 8; "works today" + CI + next-milestone sections updated.
- `infrastructure/kubernetes/README.md`, `infrastructure/terraform/README.md` —
  updated from "planned, not yet implemented" to what's actually built.
- `.github/workflows/ci.yml` — three new jobs (see §6).
- `CLAUDE.md` — current-state + roadmap updated, Stage-7 deferred row removed.

---

## 2. CLAUDE.md line count + deferred row

- **Before:** 149 lines. **After:** 149 lines (kept under 150; older Stage 4/5
  prose was compressed to make room for the Stage 7 sentence).
- The Stage-7 deferred-table row (`7 | K8s manifests, Terraform`) was **removed**;
  the table now starts at Stage 8.

---

## 3. The real `kind` deployment — exact commands and actual output

Tools `helm`, `kind`, and `terraform` were **not installed** on this machine and
no package manager was available, so pinned standalone binaries were downloaded
to a temp dir (helm v3.16.4, kind v0.30.0, terraform v1.10.5). `docker` and
`kubectl` (v1.36.1) were already present.

**Safety check first** (per the stage prompt) — `kubectl config get-contexts`
returned an **empty** list before anything was created, so Docker Desktop's
built-in Kubernetes was off; it was never touched. After `kind create cluster`,
`kubectl config current-context` → `kind-pllm`, confirmed before any install.

```
kind create cluster --name pllm
# ✓ Ensuring node image (kindest/node:v1.34.0) ... ✓ Starting control-plane ...
# Set kubectl context to "kind-pllm"
kubectl config current-context           # -> kind-pllm

kind load docker-image production-llm-platform/api:0.1.0 --name pllm
# (the local-only API image; the three datastore images pull from Docker Hub on
#  pod start — kind load hit a known multi-arch digest quirk on them, harmless)

helm install dev-api infrastructure/kubernetes/helm/production-llm-platform \
  --set devDependencies.enabled=true \
  --set secrets.anthropicApiKey=local-not-a-real-key \
  --set secrets.voyageApiKey=local-not-a-real-key
```

**Two chart bugs were caught by actually deploying** (a `helm lint` pass would
not have found either — this is exactly the "stand it up for real" bar):

1. **Service selector too broad.** The API `Service` selector was `name`+`instance`
   only, which also matched the dev datastore pods (they share those base labels).
   `port-forward svc/...` picked a Postgres pod and failed on the missing `http`
   port. **Fix:** added `app.kubernetes.io/component: api` to the API
   Deployment selector/labels and the Service selector.
2. **Startup race → Postgres `unavailable`.** The app connects to datastores once
   at boot and a store that fails to connect then stays `unavailable` until the
   pod restarts (a documented limitation). Kubernetes starts pods in parallel with
   no compose-style `depends_on`, so the API lost the race and came up permanently
   not-ready with `/ready` → `{"postgres":"unavailable",...}`. **Fix:** a
   `wait-for-datastores` initContainer (dev mode only, reusing the API image) that
   blocks until Postgres/Redis/Qdrant accept TCP before the API container starts.

After the fixes (`helm upgrade`, revision 2), final state:

```
kubectl get pods
NAME                                                        READY   STATUS    RESTARTS   AGE
dev-api-production-llm-platform-57b446685d-tbjwx            1/1     Running   0          ...
dev-api-production-llm-platform-57b446685d-znkvf            1/1     Running   0          ...
dev-api-production-llm-platform-postgres-5c9d55859b-sp2cg   1/1     Running   0          ...
dev-api-production-llm-platform-qdrant-7d66c997f5-kcxms     1/1     Running   0          ...
dev-api-production-llm-platform-redis-79c869cdf7-52dzf      1/1     Running   0          ...

# initContainer log:
kubectl logs <api-pod> -c wait-for-datastores
dev-api-production-llm-platform-postgres:5432 reachable
dev-api-production-llm-platform-redis:6379 reachable
dev-api-production-llm-platform-qdrant:6333 reachable
```

**Probes (port-forward to a free host port — 8000 on the host was already held
by the docker-compose api container, so an earlier curl on 8000 hit compose, not
kind; re-run on 18080 against the kind Service):**

```
GET /health  -> {"status":"ok","service":"api","version":"0.1.0","environment":"prod"}
GET /ready   -> {"status":"ready","checks":{"postgres":"ok","redis":"ok","qdrant":"ok"}}
GET /version -> {"service":"api","version":"0.1.0","environment":"prod"}
```

`environment:"prod"` confirms the response came from the kind pod (the chart's
default profile), not the compose stack (which runs `dev`). **`/ready` reports
all three stores `ok`.**

**Teardown:** `helm uninstall dev-api` + `kind delete cluster --name pllm`.
`kubectl config get-contexts` is empty again — clean slate restored, Docker
Desktop's Kubernetes never enabled.

---

## 4. Python quality gate — unchanged

No Python source was touched this stage. Run under `ENVIRONMENT=test`:

- `ruff check .` → **All checks passed!**
- `ruff format --check .` → **73 files already formatted**
- `mypy` → **Success: no issues found in 72 source files**
- `pytest` → **311 passed, 9 skipped** (the 9 are the opt-in live layers) — test
  count **unchanged from Stage 6's 311**.
- `scripts/build_architecture.py --check` → **architecture.html is up to date.**

---

## 5. Helm + Terraform gates — actual output

```
helm lint infrastructure/kubernetes/helm/production-llm-platform
# ==> Linting ...
# [INFO] Chart.yaml: icon is recommended
# 1 chart(s) linted, 0 chart(s) failed

helm template ... --set devDependencies.enabled=true   # renders; spot-checked:
#   path: /health   path: /ready   containerPort: 8000   (probes + port correct)

# in infrastructure/terraform/:
terraform fmt -check -recursive   # exit 0 (after an initial `terraform fmt` that
#                                   canonicalised main.tf + modules/storage/main.tf)
terraform init -backend=false     # Installed hashicorp/aws v5.100.0; initialised
terraform validate                # Success! The configuration is valid.
```

`terraform init`/`validate`/`fmt -check` were the only Terraform commands run.
The local `.terraform/` provider cache was deleted after validation (it is
git-ignored regardless).

---

## 6. CI — a `kind` job was added (not omitted)

Three new jobs in `.github/workflows/ci.yml`, all hermetic:

- **`helm`** — `helm lint` + `helm template` (prod default and dev-datastore
  renders) + a grep spot-check that the rendered probe paths/port are correct. No
  cluster.
- **`terraform`** — `terraform fmt -check -recursive` + `init -backend=false` +
  `validate`. No AWS credentials.
- **`kind-deploy`** — builds the image, spins up a real `kind` cluster
  (`helm/kind-action`), `kind load`s the image, `helm install`s with
  `devDependencies.enabled=true` and placeholder keys, waits for every rollout,
  then port-forwards and asserts `/ready` reports `postgres`/`redis`/`qdrant` all
  `ok`. **Added deliberately** — it mirrors the existing Docker job's "prove it
  actually boots" bar (this project has held that bar before) and is the CI
  counterpart to the local `kind` verification above.

---

## 7. Architecture doc

`docs/architecture.md` updated (new deployment-topology section; planned-table row
removed; header → Stage 7) and regenerated with the exact command:

```
uv run python scripts/build_architecture.py
```

`--check` then reports it up to date. `architecture.html` was never hand-edited.

---

## 8. Deviations from scope

- **Two chart additions not spelled out in the scope**, both forced by the "reach
  `/ready` → ok on a real cluster" requirement and documented above: the
  `component: api` selector scoping and the `wait-for-datastores` initContainer.
  Neither touches Python source; both are dev-path concerns (the initContainer is
  gated on `devDependencies.enabled`).
- **Tooling had to be downloaded** (helm/kind/terraform were absent, no package
  manager). Pinned standalone binaries in a temp dir; nothing installed
  system-wide, `pyproject.toml`/`uv.lock` untouched.

No other deviations. The `security` stub folder was not touched.

---

## 9. Known limitations / deliberately deferred

- **Terraform was never `plan`/`apply`-ed.** No AWS account or credentials exist
  for this project; `plan`/`apply` are a deliberate scope boundary, the same
  treatment paid LLM APIs already get — nothing spends money or touches a real
  external account without deliberate human action. `validate`/`fmt -check` prove
  the config is well-formed, **not** that a real EKS apply succeeds (a
  runtime-only error — quota, IAM edge case, region-unsupported instance type —
  would only surface at an apply this project does not run). ADR 0018.
- **The `kind` verification used placeholder API keys.** The `prod` profile
  validates the keys are *present*, not valid; `/ready` calls no provider, so the
  deployment reaches `Ready` locally. A real chat request with a placeholder key
  would fail cleanly, exactly as the CI Docker job already demonstrates.
- **Sizing is illustrative**, not load-shaped (single-AZ NAT, single-node Redis,
  no multi-AZ RDS, illustrative instance classes) — right-sizing/HA is Stage 9.
- The `wait-for-datastores` initContainer is **dev-mode only**; in production the
  managed datastores pre-exist the deploy. The underlying app-level "connect once
  at boot, stay `unavailable` until restart" limitation is unchanged (out of scope
  to change Python source this stage).
