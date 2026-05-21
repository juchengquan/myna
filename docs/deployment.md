# Deployment

Myna ships as a single ASGI app. Any platform that can run a Python
container or a uvicorn process will work.

## Docker

A production-ready image is in [`Dockerfile`](../Dockerfile):

```bash
docker build -t myna:latest .
docker run --rm -p 8000:8000 \
  -e MYNA_ENV=production \
  -e MYNA_ADMIN_API_KEY=$(openssl rand -hex 32) \
  myna:latest
```

The image:

- Uses `uv sync --frozen --no-dev` against the committed `uv.lock` for
  reproducible installs.
- Runs as a non-root `myna` user.
- Exposes a `HEALTHCHECK` that hits `GET /api/health`.

## Production checklist

- [ ] Set `MYNA_ENV=production`.
- [ ] Set `MYNA_ADMIN_API_KEY` to a high-entropy random value (e.g.
      `openssl rand -hex 32`). Without it, admin endpoints return `500`.
- [ ] Set `MYNA_MCP_API_KEYS` to a JSON object mapping at least one
      bearer token to a caller label. Without it, all `/mcp` traffic
      is rejected with `401`.
- [ ] Front the service with TLS (reverse proxy / load balancer). Do
      not expose `/mcp` over plaintext HTTP on public networks.
- [ ] Configure the reverse proxy to **disable response buffering** on
      `/mcp` so SSE chunks stream to clients.
- [ ] Decide on `MYNA_MCP_MOUNT_PATH` and pin it — clients hard-code it.
- [ ] Pin the image to a specific tag/digest in your deployment manifest.
- [ ] Set resource limits (CPU/memory) and configure horizontal scaling
      based on connection count. `stateless_http=True` makes scale-out
      safe — no session affinity needed.

## Running directly (without Docker)

```bash
uv sync --no-dev
uv run uvicorn myna.main:app \
  --host 0.0.0.0 --port 8000 \
  --workers 4 \
  --proxy-headers --forwarded-allow-ips='*'
```

Use a process supervisor (systemd, supervisord, nomad, k8s, ...) to
manage restarts.

## Observability

- **Logs**: JSON via `structlog` on stdout. Every line is a single JSON
  object — ship it as-is into your log pipeline (Loki, CloudWatch,
  Datadog, ...).
- **Audit log**: every MCP tool call emits a structured `tool_call`
  event with `tool`, `caller`, `status`, `duration_ms`, and a short
  `args_fingerprint` (SHA-256 prefix). The fingerprint correlates calls
  without leaking PII or secrets into logs.
- **Metrics**: Prometheus exposition at `GET /metrics`:
  - `myna_tool_calls_total{tool, caller, status}` counter
  - `myna_tool_call_duration_seconds{tool}` histogram
- **Health**: `GET /api/health` for liveness/readiness probes.
- **Tracing**: not built in yet. The natural hook point is OpenTelemetry
  FastAPI instrumentation in
  [`src/myna/main.py`](../src/myna/main.py) `create_app()`.

## Upgrading

1. Bump the version in [`pyproject.toml`](../pyproject.toml).
2. Run `uv lock` to refresh `uv.lock`.
3. Build and roll out the new image.
4. Because `stateless_http=True`, rolling updates do not need draining
   beyond the in-flight request window.
