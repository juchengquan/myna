# Myna

MCP service backend built on FastAPI and the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

Myna exposes:

- An **MCP server** over Streamable HTTP transport at `/mcp`, suitable for remote MCP clients.
- A **management/admin REST API** under `/api` for health, tool introspection, and operational endpoints.

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

- MCP endpoint: `http://localhost:8000/mcp`
- Admin API:    `http://localhost:8000/api/health`
- OpenAPI docs: `http://localhost:8000/docs`

## Configuration

All config is via environment variables (see `.env.example`), loaded by `pydantic-settings`.
Prefix: `MYNA_`.

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
  api/               # admin REST endpoints
  tools/             # MCP tool modules
tests/               # pytest suite
```

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
