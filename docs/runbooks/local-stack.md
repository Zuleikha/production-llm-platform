# Runbook: local Compose stack

## Start

```bash
docker compose up -d --build
./scripts/smoke_health.sh          # waits for /health to return 200
```

## Endpoints

| Service | URL | Notes |
|---------|-----|-------|
| api | http://localhost:8000 | `/health`, `/ready`, `/version`, `/metrics`, `/docs` |
| prometheus | http://localhost:9090 | Targets page shows the `api` scrape job |
| grafana | http://localhost:3000 | Prometheus datasource auto-provisioned |
| postgres | localhost:5432 | Stage 2+ (not used by the app yet) |
| redis | localhost:6379 | Stage 2+ (not used by the app yet) |
| qdrant | http://localhost:6333 | Stage 4+ (not used by the app yet) |

## Common checks

```bash
docker compose ps                  # container + health status
docker compose logs -f api         # structured JSON logs
curl -s localhost:8000/health      # expect 200
curl -s localhost:8000/metrics     # Prometheus exposition
```

## Stop

```bash
docker compose down                # keep data volumes
docker compose down -v             # ALSO delete postgres/redis/qdrant/grafana data
```

## Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `docker compose` fails to connect to the daemon | Docker Desktop not running | Start Docker Desktop, wait for the whale icon to settle |
| api container restarts repeatedly | app raised on startup | `docker compose logs api` and read the `exception` field in the JSON log |
| `/health` never returns 200 | api not bound / port clash | Check `docker compose ps`; make sure nothing else uses port 8000 |
| Prometheus target `api` is DOWN | api unhealthy or DNS | Check `http://localhost:9090/targets`, then `docker compose logs api` |
| `uv sync` behaves oddly on the host | cross-drive cache | Ensure `UV_LINK_MODE=copy` (see CLAUDE.md → Known environment quirks) |
