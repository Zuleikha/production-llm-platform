# terraform — AWS cloud provisioning

> **Status: implemented, validated, never applied** (Stage 7). See
> [ADR 0018](../../docs/adr/0018-kubernetes-and-terraform.md).

Terraform modules that provision the AWS infrastructure the platform would run
on in production. The **target provider is AWS**, stated explicitly so this is
real code against a real provider, not pseudo-infrastructure.

## Deliberate boundary: validated, not applied

This project has **no AWS account**. These modules are written to pass the
CI-safe gate and no further:

```bash
terraform init -backend=false   # local backend, no remote state, no creds
terraform validate              # config + provider-schema validity
terraform fmt -check -recursive # canonical formatting
```

`terraform plan` and `terraform apply` need real AWS credentials that do not
exist for this project and are **deliberately never run** — the same treatment
this repo already gives paid LLM APIs (nothing spends money without explicit
human action). This is a scope boundary, not an oversight. Region and sizing
values are **illustrative**, not sized for real load — right-sizing is Stage 9.

## Modules

| Module | Resource | Purpose |
|--------|----------|---------|
| `modules/networking` | VPC, public/private subnets, IGW, NAT, route tables | the network the cluster and datastores live in |
| `modules/cluster` | EKS control plane + managed node group (+ IAM roles) | runs the Helm-deployed API |
| `modules/postgres` | RDS PostgreSQL (+ subnet group, security group) | the API's `DATABASE_URL` in production |
| `modules/redis` | ElastiCache Redis replication group | the API's `REDIS_URL` (conversation cache) |
| `modules/storage` | S3 bucket (private, versioned, encrypted) | artifacts/backups |
| `modules/secrets` | Secrets Manager secret **containers** | ANTHROPIC/VOYAGE key references — **no values** |

The root module (`main.tf`) wires them together; `variables.tf` holds the
illustrative defaults; `outputs.tf` exposes the endpoints and secret ARNs the
Helm release consumes.

## No secret values in Terraform

- The RDS master **password is never set here** — `manage_master_user_password`
  has RDS generate and store it in Secrets Manager.
- The app API keys are **declared as empty Secrets Manager containers** and
  populated out-of-band; there is intentionally no `secretsmanager_secret_version`
  with a value, which would commit a secret to source and to plaintext state.
- `terraform.tfvars` is git-ignored; only `terraform.tfvars.example` (no
  secrets) is committed.

## Relationship to the Helm chart

The Helm chart's `datastores.*` URLs point at the endpoints these modules output
(RDS/ElastiCache/Qdrant) with `devDependencies.enabled=false`. The chart's
dev-only in-cluster datastores are the **local `kind`** path and are never these
managed services. See `infrastructure/kubernetes/helm/production-llm-platform/README.md`.
