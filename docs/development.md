# Development workflow

## Setup

```bash
uv sync                # creates .venv, installs runtime + dev deps
cp .env.example .env   # optional — edit as needed
```

## Run the server locally

```bash
uv run myna                              # uses MYNA_* env / .env
# or:
uv run uvicorn myna.main:app --reload    # explicit reload mode
```

Useful URLs:

- `http://localhost:8000/api/health`
- `http://localhost:8000/api/admin/tools`
- `http://localhost:8000/docs`        — Swagger UI
- `http://localhost:8000/mcp/`        — MCP Streamable HTTP endpoint

## Tests

```bash
uv run pytest                              # all tests
uv run pytest -v tests/test_weather_tool.py
uv run pytest --cov=myna --cov-report=term-missing
```

The pytest suite exercises tools **in-process** against the `FastMCP`
instance — no HTTP layer. Each test gets a fresh app via the `client`
fixture in [`tests/conftest.py`](../tests/conftest.py).

## Lint and type-check

```bash
uv run ruff check .          # lint
uv run ruff check . --fix    # auto-fix
uv run mypy src              # types
```

Lint config is in [`pyproject.toml`](../pyproject.toml) under
`[tool.ruff]`. `B008` is intentionally disabled inside `src/myna/api/`
because FastAPI's `Depends(...)`/`Header(...)` defaults are idiomatic.

## End-to-end smoke test

`pytest` does not exercise the Streamable HTTP transport. For that, run
the server in one shell and the smoke script in another:

```bash
# shell 1
uv run myna

# shell 2
uv run python scripts/smoke_test.py
# or against a non-default URL:
uv run python scripts/smoke_test.py --url http://localhost:8000/mcp/
```

The script initializes a real MCP session, lists tools, and calls
`ping` / `echo` / `get_weather`. It exits non-zero if any expected tool
is missing.

## Adding / updating dependencies

```bash
uv add fastapi-something                    # runtime dep
uv add --group dev pytest-something         # dev dep
uv remove fastapi-something
uv sync                                     # apply changes to .venv
```

Always commit `uv.lock` together with the `pyproject.toml` change.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and every PR:

1. Install uv (`astral-sh/setup-uv@v3`).
2. `uv sync --frozen` against `uv.lock`.
3. `uv run ruff check .`
4. `uv run mypy src`
5. `uv run pytest --cov=myna --cov-report=term-missing`

The job matrix covers Python 3.11 and 3.12.
