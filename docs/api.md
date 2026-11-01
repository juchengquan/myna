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

The matching endpoints for the other MCP primitives:

- `GET /api/admin/resources` — both static resources and templates,
  with `uri`, `description`, `mime_type`, and `is_template` per entry.
- `GET /api/admin/prompts` — registered prompts with their `arguments`
  (name, description, required).

```bash
curl -H "Authorization: Bearer $MYNA_ADMIN_API_KEY" \
     http://localhost:8000/api/admin/tools
```

```json
{
  "tools": [
    { "name": "ping",        "description": "Health check tool — returns 'pong'." },
    { "name": "echo",        "description": "Echo back the provided message." },
    { "name": "get_weather", "description": "Return the current weather for a location, via Open-Meteo." }
  ]
}
```

### `GET /metrics`

Prometheus exposition endpoint. Authentication follows the admin rules
above, using `MYNA_METRICS_API_KEY`: open in development if the key is
unset, `500` in `staging`/`production` if it is unset, and otherwise
`401` without the right bearer token. Point Prometheus at it with:

```yaml
scrape_configs:
  - job_name: myna
    authorization:
      credentials: <MYNA_METRICS_API_KEY>
```

The `tool`, `uri`, and `name` labels only use registered values, so
clients can't create new series by sending arbitrary input. A templated
resource is labelled by its template, such as
`weather://locations/{location}`, and anything unregistered is labelled
`unknown`. Audit log lines and spans keep the raw value.

Currently exposes:

- `myna_tool_calls_total{tool, caller, status}` — counter of MCP tool calls.
- `myna_tool_call_duration_seconds{tool}` — latency histogram per tool.
- `myna_tool_cache_total{tool, outcome}` — tool result-cache outcomes; `outcome` is `hit` or `miss`. Only emitted for tools decorated with `@cached`.
- `myna_resource_reads_total{uri, caller, status}` — counter of MCP resource reads.
- `myna_resource_read_duration_seconds{uri}` — latency histogram per resource URI or template.
- `myna_prompt_gets_total{name, caller, status}` — counter of MCP prompt fetches.
- `myna_prompt_get_duration_seconds{name}` — latency histogram per prompt name.
- `myna_rate_limit_hits_total{key_kind}` — counter of MCP requests rejected
  by the rate limiter. `key_kind` is `caller` for authenticated requests or
  `ip` for anonymous ones.

### `HTTP 429` on `/mcp`

When `MYNA_MCP_RATE_LIMIT_PER_MINUTE` is non-zero and a caller (or IP)
exceeds its burst budget, requests are rejected with:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 20
Content-Type: application/json

{"error": "rate_limited", "retry_after": 19.95}
```

`Retry-After` is the conservative integer-second hint (per RFC 7231);
`retry_after` in the JSON body is the precise float estimate.

The `caller` label resolves from `MYNA_MCP_API_KEYS` (see
[configuration.md](configuration.md)); requests in anonymous-mode dev
report `caller="anonymous"`.

## OpenAPI

The full schema is auto-generated and served at:

- `GET /openapi.json`
- `GET /docs` — Swagger UI
- `GET /redoc` — ReDoc
