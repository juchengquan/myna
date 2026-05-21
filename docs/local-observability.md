# Local observability stack

Myna emits three pillars of telemetry — structured JSON logs, Prometheus
metrics, and OTel spans. Locally, none of that is visible without
somewhere to send it. The repo ships a one-command `docker-compose.yml`
that brings up Myna alongside the minimum needed to actually *see* what
it produces.

## What you get

| Service | URL | Purpose |
| --- | --- | --- |
| Myna | http://localhost:8000 | The app. `/api`, `/mcp`, `/metrics`. |
| Jaeger UI | http://localhost:16686 | View traces. Search the `myna` service. |
| Prometheus | http://localhost:9090 | Ad-hoc PromQL over the scraped series. |

Logs are streamed to stdout of the Myna container — `docker compose
logs -f myna` is the dev-time tail.

## Bring it up

```bash
docker compose up --build
```

That builds the Myna image from the repo's `Dockerfile`, pulls Jaeger
and Prometheus, and starts everything. First run is slow; later runs
reuse the cache.

Once it's up, drive some traffic:

```bash
uv run python scripts/smoke_test.py
```

Then:

- **Jaeger** — open http://localhost:16686, pick `myna` from the
  service dropdown, click *Find Traces*. Each `/mcp` POST shows up as
  one HTTP span (FastAPI auto-instrumentation) with the
  `mcp.tool.call <name>`, `mcp.resource.read <uri>`, or
  `mcp.prompt.get <name>` span nested under it.
- **Prometheus** — open http://localhost:9090, try queries like:
  - `myna_tool_calls_total` — counts by tool/caller/status
  - `rate(myna_tool_call_duration_seconds_sum[1m])` — avg latency
  - `myna_rate_limit_hits_total` — should stay flat unless you exceed
    `MYNA_MCP_RATE_LIMIT_PER_MINUTE`
- **Logs** — `docker compose logs -f myna | jq .` shows the structured
  JSON, including the `trace_id` / `span_id` fields on every audit
  line so you can paste one into Jaeger's *Lookup by Trace ID* and
  jump straight to the trace.

## Bring it down

```bash
docker compose down          # stop containers
docker compose down -v       # also drop volumes (none defined, but habit)
```

## File layout

| File | What it is |
| --- | --- |
| `docker-compose.yml` | The stack. Edit ports / images here. |
| `deploy/observability/prometheus.yml` | Prometheus scrape config — single static target (`myna:8000`). |
| `Dockerfile` | Same image used in production; the compose stack just sets dev env vars on it. |

## Production note

This compose file is **for local development only**. In production,
Myna should run as a container of its own (built from the same
`Dockerfile`) talking to your existing observability backends:

- OTLP collector reachable at whatever URL is in
  `MYNA_OTEL_EXPORTER_ENDPOINT`.
- A Prometheus scrape (or an exporter sidecar) hitting Myna's
  `/metrics`.
- Your log shipper following stdout.

See [`deployment.md`](deployment.md) for the production checklist.
