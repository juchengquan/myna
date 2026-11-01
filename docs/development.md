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
uv run pytest -v tests/weather/test_tool.py
uv run pytest --cov=myna --cov-report=term-missing
```

Most of the pytest suite exercises tools **in-process** against the
`MCPServer` instance — no HTTP layer. Each test gets a fresh app via the
`client` fixture in [`tests/conftest.py`](../tests/conftest.py).
[`tests/test_wire.py`](../tests/test_wire.py) starts real local servers
and covers stateless and stateful mode, and both protocol eras, over
Streamable HTTP.

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

To check a running server, including the real Open-Meteo upstream, run
the server in one shell and the smoke script in another:

```bash
# shell 1
uv run myna

# shell 2
uv run python scripts/smoke_test.py
# or against a non-default URL:
uv run python scripts/smoke_test.py --url http://localhost:8000/mcp/
```

The script connects as a legacy client and as a 2026-07-28 client, and
exercises the tools, resources, and prompts. It exits non-zero if anything expected is
missing, if progress notifications don't arrive, or if `confirm_action`
errors or doesn't finish within 15 seconds. The one exception is a legacy
client on a stateless server, where a protocol error is expected.

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

1. Install uv (`astral-sh/setup-uv@v7`).
2. `uv sync --frozen` against `uv.lock`.
3. `uv run ruff check .`
4. `uv run mypy src`
5. `uv run pytest --cov=myna --cov-report=term-missing`

The job matrix covers Python 3.11 through 3.14, which matches
`requires-python` in `pyproject.toml`.

To support a new Python version, add it to the matrix, raise the cap in
`requires-python`, and run `uv lock`. Move the Docker image to a new version
in a separate PR, and only to a version already in the matrix. Dependabot
doesn't propose Python upgrades for the image, and the image build fails
rather than downloading a different Python.

A `docker` job builds the image, starts it with production settings, and
checks `/api/health` and an authenticated `/mcp` request. It then runs
`scripts/check_image_sqlite.sh`, which checks that database keys on SQLite work
and survive a new container, on a named volume and on a bind mount.

A `postgres` job runs the key store tests in `tests/keys` against PostgreSQL,
which the other test jobs skip. Run them locally by setting
`MYNA_TEST_POSTGRES_URL`.

A `template` job runs the commands in the README's "Start a new server"
section as written, then checks that the result passes the tests, ruff, and
mypy.

A separate `audit` job runs `pip-audit` against the locked runtime
dependencies, on every push and PR and weekly, because advisories appear
without code changes. Dependabot opens weekly update PRs for Python
dependencies, GitHub Actions, and the Docker base image.
