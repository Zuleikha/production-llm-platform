# examples

Runnable examples against the platform. Examples are added as each stage lands
real capability — Stage 1 only has the foundation endpoints to demonstrate.

| Example | Stage | Status |
|---------|-------|--------|
| `check_health.py` — query `/health`, `/ready`, `/version` | 1 | ✅ available |
| Chat / completion request | 2 (api) | planned, not yet implemented |
| Agent with tool use | 3 (agents) | planned, not yet implemented |
| RAG question over a document set | 4 (rag) | planned, not yet implemented |
| Evaluation run | 6 (mlops) | planned, not yet implemented |

## Running

Start the stack first (`docker compose up -d --build`), then:

```bash
uv run python examples/check_health.py
```
