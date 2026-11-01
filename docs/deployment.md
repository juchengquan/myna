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
  -e MYNA_METRICS_API_KEY=$(openssl rand -hex 32) \
  -e MYNA_MCP_API_KEYS="{\"$(openssl rand -hex 32)\":\"client-a\"}" \
  -e MYNA_MCP_ALLOWED_HOSTS='["myna.example.com"]' \
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
- [ ] Set `MYNA_METRICS_API_KEY` the same way and give it to your
      Prometheus scrape config. Without it, `/metrics` returns `500`.
- [ ] Set `MYNA_MCP_API_KEYS` to a JSON object mapping at least one
      bearer token to a caller label. Without it, all `/mcp` traffic
      is rejected with `401`.
- [ ] Set `MYNA_MCP_ALLOWED_HOSTS` to the hostnames clients use, such as
      `'["myna.example.com"]'`. Without it, all `/mcp` traffic through a
      non-localhost name is rejected with `421`. Behind a reverse proxy,
      make sure it forwards the original `Host` header. Set
      `MYNA_MCP_ALLOWED_ORIGINS` too if browser-based clients connect.
- [ ] Tune `MYNA_MCP_RATE_LIMIT_PER_MINUTE` to a value appropriate for
      your expected client behavior. The default (`120`) is a sensible
      starting point; raise it for high-throughput agents, set to `0`
      only if a downstream load balancer already enforces limits.
- [ ] If you run more than one replica, note that the token-bucket
      state is in-process. Each replica enforces the limit
      independently, so the effective ceiling is roughly
      `replicas * MYNA_MCP_RATE_LIMIT_PER_MINUTE`. For exact global
      enforcement, swap in a Redis-backed limiter (the `RateLimiter`
      interface in `src/myna/rate_limit.py` is intentionally small).
- [ ] Front the service with TLS (reverse proxy / load balancer). Do
      not expose `/mcp` over plaintext HTTP on public networks.
- [ ] Configure the reverse proxy to **disable response buffering** on
      `/mcp` so SSE chunks stream to clients.
- [ ] Decide on `MYNA_MCP_MOUNT_PATH` and pin it — clients hard-code it.
- [ ] Pin the image to a specific tag/digest in your deployment manifest.
- [ ] Set resource limits (CPU/memory) and configure horizontal scaling
      based on connection count. The default stateless mode makes
      scale-out safe — no session affinity needed. If you set
      `MYNA_MCP_STATELESS=false`, configure session affinity for
      multiple replicas if legacy clients connect. 2026-07-28 clients
      never need it.

## Running directly (without Docker)

```bash
uv sync --no-dev
uv run uvicorn myna.main:app \
  --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips='<your-proxy-ip>'
```

Set `--forwarded-allow-ips` to your proxy's address rather than `*`.
Otherwise any client can claim any IP in `X-Forwarded-For` and sidestep
the per-address limits. Without `--proxy-headers` behind a proxy, all
clients share the proxy's address, including its failed-auth quota.
The Docker image's default command doesn't set these flags, so pass them
if you run it behind a proxy.

Scale by running more single-worker instances rather than adding
`--workers`. The cache, rate limiter, and Prometheus counters are all
per process, so with several workers on one port each scrape of
`/metrics` reports only the worker that answered it.

Use a process supervisor (systemd, supervisord, nomad, k8s, ...) to
manage restarts.

## Observability

- **Logs**: JSON via `structlog` on stdout. Every line is a single JSON
  object — ship it as-is into your log pipeline (Loki, CloudWatch,
  Datadog, ...).
- **Audit log**: every MCP operation emits a structured event:
  - `tool_call`     — `tool`, `caller`, `status`, `duration_ms`, `args_fingerprint`
  - `resource_read` — `uri`, `caller`, `status`, `duration_ms`
  - `prompt_get`    — `name`, `caller`, `status`, `duration_ms`, `args_fingerprint`
  The `args_fingerprint` is a SHA-256 prefix of the arguments and lets
  you correlate calls without leaking PII or secrets into logs.
  When tracing is enabled, every line also carries `trace_id` and
  `span_id` of the active span — drop them into your tracing UI to
  jump from a log line straight to the trace.
- **Metrics**: Prometheus exposition at `GET /metrics`, protected by
  `MYNA_METRICS_API_KEY` outside development:
  - `myna_tool_calls_total{tool, caller, status}` counter +
    `myna_tool_call_duration_seconds{tool}` histogram
  - `myna_tool_cache_total{tool, outcome}` counter (only for tools
    decorated with `@cached`)
  - `myna_resource_reads_total{uri, caller, status}` counter +
    `myna_resource_read_duration_seconds{uri}` histogram
  - `myna_prompt_gets_total{name, caller, status}` counter +
    `myna_prompt_get_duration_seconds{name}` histogram
  - `myna_rate_limit_hits_total{key_kind}` counter
- **Traces** (opt-in): set `MYNA_OTEL_ENABLED=true` and point
  `MYNA_OTEL_EXPORTER_ENDPOINT` at any OTLP/HTTP collector
  (`http://collector:4318/v1/traces`). You get:
    * one span per HTTP request via FastAPI auto-instrumentation
      (`api/health` and `/metrics` are excluded as noise)
    * one nested span per MCP operation:
      - `mcp.tool.call <name>`
      - `mcp.resource.read <uri>`
      - `mcp.prompt.get <name>`
    * each carries `mcp.caller`, `mcp.status`, `mcp.duration_ms`, plus
      kind-specific attributes (`mcp.tool.name`, `mcp.resource.uri`,
      `mcp.prompt.name`, `mcp.args_fingerprint` where applicable)
    * exceptions recorded as span events and the span marked as
      `StatusCode.ERROR`
    * audit log lines carry the active `trace_id` / `span_id` so logs
      and traces correlate
  Resource attributes: `service.name`, `service.version`,
  `deployment.environment`.
  The MCP SDK also records its own span for each MCP message, under the
  `mcp-python-sdk` tracer.
- **Health**: `GET /api/health` for liveness/readiness probes.

## Upgrading

1. Bump the version in [`pyproject.toml`](../pyproject.toml).
2. Run `uv lock` to refresh `uv.lock`.
3. Build and roll out the new image.
4. In the default stateless mode, rolling updates do not need draining
   beyond the in-flight request window. With `MYNA_MCP_STATELESS=false`,
   clients on a replaced instance lose their session and must
   re-initialize.
