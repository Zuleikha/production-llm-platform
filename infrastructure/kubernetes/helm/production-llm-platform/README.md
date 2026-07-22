# production-llm-platform Helm chart

Deploys the `api` service (the FastAPI app from the repo `Dockerfile`) to
Kubernetes, with liveness/readiness probes wired to the app's real `/health`
and `/ready` endpoints.

> **Why Helm and not raw manifests, and why the in-cluster datastores below are
> dev-only:** see [ADR 0018](../../../../docs/adr/0018-kubernetes-and-terraform.md).

## What it deploys

| Object | Purpose |
|--------|---------|
| `Deployment` | the `api` pods (configurable `replicaCount`), non-root, with probes + resource requests/limits |
| `Service` (ClusterIP) | stable in-cluster address for the API on port `8000` |
| `ConfigMap` | non-secret settings (`ENVIRONMENT`, `LOG_*`, `API_PORT`, optional OTLP endpoint) |
| `Secret` | datastore URLs + API keys, injected at install — **never committed with real values** |
| `HorizontalPodAutoscaler` | optional (`autoscaling.enabled`) |
| dev datastores | optional Postgres/Redis/Qdrant, gated by `devDependencies.enabled` — **dev only, see below** |

## Probes

- `livenessProbe` → `GET /health` — pure liveness, touches no datastore. A
  failed liveness probe restarts the pod, so it must not depend on Postgres.
- `readinessProbe` → `GET /ready` — probes every configured datastore and
  returns `503` when any is down, so the kubelet routes traffic away without
  killing the pod. This is the probe that arms the `/ready` alert (ADR 0016).

## The datastore split — read this before deploying

The chart supports two mutually-exclusive datastore modes:

### Production — external managed datastores (the real path)

Set `devDependencies.enabled=false` (the default) and point the URLs at the
**managed services Terraform provisions** (`infrastructure/terraform/`): RDS for
Postgres, ElastiCache for Redis, and a reachable Qdrant.

```bash
helm install prod-api ./production-llm-platform \
  --set datastores.databaseUrl='postgresql://...rds...' \
  --set datastores.redisUrl='redis://...elasticache...' \
  --set datastores.qdrantUrl='http://...qdrant...' \
  --set-file secrets.anthropicApiKey=/path/anthropic.key \
  --set-file secrets.voyageApiKey=/path/voyage.key
```

Under the `prod` profile the API **refuses to boot** until all three URLs and
both API keys are present (fail loud — ADR 0005/0006/0011).

### Local / kind — in-cluster dev datastores (verification only)

`devDependencies.enabled=true` deploys minimal, **ephemeral** (`emptyDir`,
single replica, no backups, no auth hardening) Postgres/Redis/Qdrant using the
exact images pinned in `docker-compose.yml`, and auto-fills the datastore URLs
to point at them. This exists **solely** so a local `kind` cluster can bring
`/ready` to `ok` end-to-end without any cloud account.

> ⚠️ **These in-cluster datastores are NOT the production path.** They hold no
> real data, survive no restart, and must never back a production release.
> Production uses the Terraform-managed services above. Do not conflate the two.

```bash
helm install dev-api ./production-llm-platform \
  --set devDependencies.enabled=true \
  --set secrets.anthropicApiKey=local-not-a-real-key \
  --set secrets.voyageApiKey=local-not-a-real-key
```

(The `prod` profile validates that the API keys are *present*, not that they are
valid — `/ready` never calls Anthropic or Voyage, so local placeholders bring
the deployment fully `Ready`. A real chat request with a placeholder key fails
cleanly, exactly as in the CI Docker job.)

## Local verification with kind

```bash
kind create cluster --name pllm
kind load docker-image production-llm-platform/api:0.1.0 --name pllm
helm install dev-api ./production-llm-platform \
  --set devDependencies.enabled=true \
  --set secrets.anthropicApiKey=local-not-a-real-key \
  --set secrets.voyageApiKey=local-not-a-real-key
kubectl rollout status deployment/dev-api-production-llm-platform
kubectl port-forward svc/dev-api-production-llm-platform 8000:8000
curl http://localhost:8000/health
curl http://localhost:8000/ready   # expect every store "ok"
```

## Lint / render (no cluster needed)

```bash
helm lint ./production-llm-platform
helm template dev-api ./production-llm-platform --set devDependencies.enabled=true
```

## No secrets in values

API keys default to empty and must be injected at install. The `Secret`
template omits any key whose value is blank, so nothing empty or fake is baked
into a committed artifact. The only credentials that appear in this chart's
files are the ephemeral dev-datastore placeholders (`platform`/`platform`),
identical to `docker-compose.yml`'s local defaults — never a production secret.
