# kubernetes

> **Status: implemented** (Stage 7). See
> [ADR 0018](../../docs/adr/0018-kubernetes-and-terraform.md).

Kubernetes deployment for the `api` service, packaged as a **Helm chart** (not
raw manifests — parameterized via `values.yaml`, ADR 0018).

## Layout

```
helm/production-llm-platform/
  Chart.yaml
  values.yaml
  README.md                     # chart usage, the datastore split, kind steps
  templates/
    deployment.yaml             # api pods; probes -> /health (liveness), /ready (readiness)
    service.yaml                # ClusterIP on 8000
    configmap.yaml              # non-secret settings
    secret.yaml                 # datastore URLs + API keys, injected at install
    hpa.yaml                    # optional HorizontalPodAutoscaler
    dev-datastores.yaml         # dev-only Postgres/Redis/Qdrant (gated)
    _helpers.tpl · NOTES.txt
```

## Quick reference

```bash
# lint + render (no cluster)
helm lint helm/production-llm-platform
helm template rel helm/production-llm-platform --set devDependencies.enabled=true

# local end-to-end on kind
kind create cluster --name pllm
kind load docker-image production-llm-platform/api:0.1.0 --name pllm
helm install dev-api helm/production-llm-platform \
  --set devDependencies.enabled=true \
  --set secrets.anthropicApiKey=local-not-a-real-key \
  --set secrets.voyageApiKey=local-not-a-real-key
kubectl port-forward svc/dev-api-production-llm-platform 8000:8000
curl localhost:8000/ready     # every store "ok"
```

## Probes

- **liveness** → `GET /health` (touches no datastore — a DB blip must not restart pods)
- **readiness** → `GET /ready` (probes every configured store; 503 routes traffic away)

## The datastore split — dev vs production

The chart can deploy minimal **dev-only** in-cluster Postgres/Redis/Qdrant
(`devDependencies.enabled=true`), which exist **solely** so a local `kind`
cluster can bring `/ready` to `ok` end-to-end without a cloud account. They are
ephemeral and must never hold real data.

**Production points `DATABASE_URL`/`REDIS_URL`/`QDRANT_URL` at the managed
services Terraform provisions** (`infrastructure/terraform/` — RDS, ElastiCache,
Qdrant) with `devDependencies.enabled=false`. Do not conflate the two. Full
detail is in the chart's own README.
