# Architecture Decision Records

One ADR per significant decision — never a single combined file. ADRs are
immutable once accepted: to change a decision, add a new ADR that supersedes the
old one and update the old one's status.

## Format

Every ADR uses: **Context** (the forces at play) → **Decision** (what we chose)
→ **Consequences** (positive *and* negative), plus **Status** and **Date**.

## Index

| ADR | Title | Status | Stage |
|-----|-------|--------|-------|
| [0001](0001-stack-selection.md) | Stack selection | Accepted | 1 |
| [0002](0002-repo-structure.md) | Repository structure | Accepted | 1 |
| [0003](0003-configuration-approach.md) | Configuration approach | Accepted | 1 |
| [0004](0004-streaming-transport.md) | Streaming transport for chat completions | Accepted | 2 |
| [0005](0005-datastore-connection-pooling.md) | Datastore connection pooling and readiness | Accepted | 2 |
| [0006](0006-agent-loop-and-orchestration.md) | Agent loop, orchestration and the model client | Accepted | 3 |
| [0007](0007-database-migrations.md) | Database migrations: raw SQL, forward-only | Accepted | 3 |
| [0008](0008-conversation-caching-strategy.md) | Conversation caching: Redis read-through, Postgres owns the truth | Accepted | 3 |
| [0009](0009-hermetic-llm-testing.md) | The test profile cannot call Anthropic | Accepted | 3 |
| [0010](0010-pre-rendered-diagrams.md) | Architecture diagrams are pre-rendered inline SVG | Accepted | 3 |
| [0011](0011-embeddings-provider.md) | Embeddings via Voyage AI, behind a profile-keyed hermetic seam | Accepted | 4 |
| [0012](0012-qdrant-vector-store.md) | Qdrant collection design and the switch to `qdrant-client` | Accepted | 4 |
| [0013](0013-citation-shape.md) | Citations are a new top-level response field of typed provenance | Accepted | 4 |
| [0014](0014-prompt-injection-mitigation.md) | Prompt-injection mitigation for retrieved document text | Accepted | 4 |
| [0015](0015-live-provider-contract-test.md) | An opt-in live contract test for both external providers | Accepted | 4 |
| [0016](0016-observability-stack.md) | Observability stack: OTel traces → Collector → Tempo, Grafana-native alerting | Accepted | 5 |
| [0017](0017-rag-evaluation-and-regression-gate.md) | RAG evaluation: recall@k/MRR, CI-vs-opt-in two-tier split, regression baseline | Accepted | 6 |
| [0018](0018-kubernetes-and-terraform.md) | Kubernetes via Helm, dev-mode vs Terraform-managed datastores, validate-not-apply AWS Terraform | Accepted | 7 |

## Conventions

- Filename: `NNNN-kebab-case-title.md`, zero-padded, sequential.
- Status: `Proposed` → `Accepted` → (`Superseded by NNNN` | `Deprecated`).
- Record the trade-off you accepted, not just the option you picked.
