# Architecture

Myna is a single FastAPI application that hosts:

1. An **MCP server** (via the official [`mcp`](https://github.com/modelcontextprotocol/python-sdk)
   Python SDK's `FastMCP`), exposed over the **Streamable HTTP** transport.
2. A **management/admin REST API** for health checks and tool introspection.

Both surfaces are served from the same ASGI process and share the same
configuration and logging stack.

## Request flow

```
                       ┌────────────────────────────────────────┐
                       │           FastAPI (myna.main)          │
MCP client  ── /mcp ──▶│  mount: FastMCP.streamable_http_app()  │
                       │                                        │
HTTP client ── /api ──▶│  router: myna.api (health, admin)      │
                       └────────────────────────────────────────┘
                                       │
                              app.state.mcp = FastMCP
                              │
                              ├── tools (myna.tools.*)
                              ├── resources (future)
                              └── prompts (future)
```

- The MCP app is mounted at `MYNA_MCP_MOUNT_PATH` (default `/mcp`). Its
  internal Streamable HTTP path is set to `/` so the externally-visible
  endpoint is exactly `/mcp`.
- The FastMCP `session_manager` is started/stopped by FastAPI's
  `lifespan` context manager — see `src/myna/main.py`.
- A fresh `FastMCP` instance is built per app (`build_mcp()` in
  `src/myna/mcp_server.py`). `StreamableHTTPSessionManager.run()` can
  only be called once per instance, so we do **not** cache it as a
  module-level singleton.

## Package layout

```
src/myna/
├── __init__.py         # package version
├── __main__.py         # `myna` console script (uvicorn launcher)
├── main.py             # create_app(), lifespan, mount, exception handler
├── config.py           # pydantic-settings Settings (MYNA_* env vars)
├── logging_config.py   # structlog JSON logging
├── mcp_server.py       # build_mcp() + tool registration
├── api/                # admin REST surface
│   ├── __init__.py     #   /api router aggregation
│   ├── health.py       #   GET /api/health
│   └── admin.py        #   GET /api/admin/tools (bearer auth)
└── tools/              # MCP tool modules (one per topic)
    ├── example.py      #   ping, echo
    └── weather.py      #   get_weather (dummy)
```

## Key design choices

- **Mount, don't fork.** FastMCP is mounted into FastAPI rather than run
  as a separate process, so the admin API and the MCP server share state
  (`app.state.mcp`) and are deployed as one unit.
- **Per-app MCP instance.** Required by the SDK's session manager
  lifecycle; also makes `TestClient` and multi-worker setups predictable.
- **Pluggable tool registration.** Each `tools/<name>.py` exposes a
  `register(mcp: FastMCP)` function called from
  `mcp_server._register_tools()`. Adding a tool means adding a module
  and one line in that registrar — no decorator magic at import time,
  so test isolation stays clean.
- **Stateless HTTP.** `FastMCP(stateless_http=True)` — each request is
  self-contained, which is friendlier to horizontal scaling and
  load-balancing than session-affinity HTTP.
