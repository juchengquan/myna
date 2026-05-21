# Admin REST API

The admin/management API lives under `/api`. It is intended for
operators and dashboards — **not** for LLM clients (those talk to
`/mcp`). Interactive OpenAPI docs are at `/docs`.

## Authentication

- In `MYNA_ENV=development`, the admin endpoints are open if
  `MYNA_ADMIN_API_KEY` is unset (convenient for local dev).
- In `staging`/`production`, `MYNA_ADMIN_API_KEY` **must** be set;
  otherwise admin endpoints return `500`.
- When a key is configured, send it as a bearer token:

  ```
  Authorization: Bearer <MYNA_ADMIN_API_KEY>
  ```

  Missing or wrong tokens return `401`.

## Endpoints

### `GET /api/health`

Liveness/readiness probe. Always public.

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

### `GET /api/admin/tools`

List the MCP tools currently registered on the server. Useful for
operator dashboards and registration sanity checks.

```bash
curl -H "Authorization: Bearer $MYNA_ADMIN_API_KEY" \
     http://localhost:8000/api/admin/tools
```

```json
{
  "tools": [
    { "name": "ping",        "description": "Health check tool — returns 'pong'." },
    { "name": "echo",        "description": "Echo back the provided message." },
    { "name": "get_weather", "description": "Return a (fake, deterministic) current-weather report for a location." }
  ]
}
```

### `GET /metrics`

Prometheus exposition endpoint. Public (no auth) — designed to be scraped
by a Prometheus server inside your network. Place it behind a firewall or
a path-based ACL on your reverse proxy if your perimeter requires it.

Currently exposes:

- `myna_tool_calls_total{tool, caller, status}` — counter of MCP tool calls.
- `myna_tool_call_duration_seconds{tool}` — latency histogram per tool.

The `caller` label resolves from `MYNA_MCP_API_KEYS` (see
[configuration.md](configuration.md)); requests in anonymous-mode dev
report `caller="anonymous"`.

## OpenAPI

The full schema is auto-generated and served at:

- `GET /openapi.json`
- `GET /docs` — Swagger UI
- `GET /redoc` — ReDoc
