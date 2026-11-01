# Myna

MCP service backend built on **FastAPI**, with the official
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
mounted as a sub-app to speak the MCP wire protocol.

- FastAPI owns the process — lifespan, routing, OpenAPI, the admin API,
  and any HTTP routes you add.
- `FastMCP` is mounted at `/mcp` and is responsible only for the MCP
  protocol (Streamable HTTP, JSON-RPC, sessions, tool schemas).
- Tools are plain Python functions in [`src/myna/tools/`](src/myna/tools/).

Surfaces:

- **MCP server** at `/mcp` over Streamable HTTP transport, for remote MCP clients.
- **Management/admin REST API** under `/api` — health, tool introspection, and operational endpoints.

> Deeper docs live in [`docs/`](docs/) — architecture, configuration,
> writing tools, client integration, deployment, and the dev workflow.
> The "FastAPI hosts, SDK speaks MCP" split is explained in
> [`docs/architecture.md`](docs/architecture.md). To poke the
> traces/metrics/logs surface locally with one command, see
> [`docs/local-observability.md`](docs/local-observability.md).

## Quickstart

Myna uses [uv](https://docs.astral.sh/uv/) for dependency and project management.

```bash
uv sync                 # create .venv and install runtime + dev deps
cp .env.example .env
uv run myna             # or: uv run uvicorn myna.main:app --reload
```

To add a dependency:

```bash
uv add <package>              # runtime dep
uv add --group dev <package>  # dev-only dep
```

Then:

- MCP endpoint: `http://localhost:8000/mcp/`
- Admin API:    `http://localhost:8000/api/health`
- OpenAPI docs: `http://localhost:8000/docs`

## Configuration

All config is via environment variables (see `.env.example`), loaded by `pydantic-settings`.
Prefix: `MYNA_`.

The server runs in stateless HTTP mode by default, so replicas need no
session affinity. Progress notifications and the `summarize_via_sampling`
and `confirm_action` tools need `MYNA_MCP_STATELESS=false`; see
[`docs/architecture.md`](docs/architecture.md#stateless-and-stateful-mode).

## Adding tools

Register MCP tools in `src/myna/tools/`. Each module exposes a `register(mcp)` function
that adds tools/resources/prompts to the shared `FastMCP` instance. See `tools/example.py`.

## Layout

```
src/myna/
  main.py            # FastAPI app, mounts MCP + admin API
  config.py          # pydantic-settings config
  logging_config.py  # structlog setup
  mcp_server.py      # FastMCP instance + tool registration
  middleware.py      # auth + rate limiting on /mcp
  rate_limit.py      # token bucket
  cache.py           # @cached TTL decorator
  observability.py   # metrics, audit log, spans
  tracing.py         # OpenTelemetry setup
  weather_client.py  # Open-Meteo HTTP client
  api/               # admin REST endpoints
  tools/             # MCP tool modules
  resources/         # MCP resource modules
  prompts/           # MCP prompt modules
tests/               # pytest suite
```

See [`docs/architecture.md`](docs/architecture.md#package-layout) for the full tree.

## Docker

```bash
docker build -t myna .
docker run --rm -p 8000:8000 --env-file .env myna
```

## Tests

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

### End-to-end smoke test

`pytest` starts its own local servers and doesn't call Open-Meteo. To
verify a running server, including the real weather upstream, run the
server in one shell and the smoke test in another:

```bash
# shell 1
uv run myna

# shell 2
uv run python scripts/smoke_test.py
# or against a non-default URL:
uv run python scripts/smoke_test.py --url http://localhost:8000/mcp/
```

It initializes a session and exercises the tools, resources, and prompts.
Against a stateful server it also checks progress notifications and both
interactive tools, and fails if one doesn't finish within 15 seconds.
