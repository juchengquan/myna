# Architecture

## TL;DR — FastAPI is the host; the MCP SDK only owns `/mcp`

Myna is a **FastAPI application**. FastAPI owns the process: lifespan,
routing, dependency injection, OpenAPI docs, exception handling, the
admin REST surface, and any future HTTP endpoints you add.

The [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
(`FastMCP`) is used as a **mounted sub-application** for one thing only:
correctly speaking the MCP protocol on the `/mcp` URL. Everything else
— tools as plain Python, your own routes, middleware, auth, settings —
stays in normal FastAPI / Python idioms.

So the layering is:

| Layer | Owned by | Why |
| --- | --- | --- |
| ASGI host, lifespan, routing, OpenAPI, admin API | **FastAPI** | Ergonomic, familiar, full control |
| MCP wire protocol on `/mcp` (JSON-RPC framing, Streamable HTTP + SSE, session handling, `initialize`/`tools/list`/`tools/call`, schema generation from type hints) | **`FastMCP`** (mounted) | Spec-compliant, tracks MCP revisions for us |
| Tool implementations | **Plain Python** in `myna/tools/*` | No SDK lock-in beyond the `@mcp.tool()` decorator |

We use the SDK rather than reimplementing MCP as FastAPI routes because
the wire protocol is non-trivial (JSON-RPC, SSE streaming,
`Mcp-Session-Id` semantics, capability negotiation) and the spec is
still evolving. Mounting keeps our surface area small.

## Surfaces

The single ASGI process exposes two surfaces, sharing config, logging,
and lifespan:

1. **MCP server** at `/mcp` — handled by the mounted `FastMCP` sub-app
   (Streamable HTTP transport).
2. **Management/admin REST API** at `/api` — pure FastAPI routes:
   health checks and tool introspection.

## Request flow

```
                       ┌──────────────────────────────────────────────┐
                       │          FastAPI app  (myna.main)            │
                       │  ── owns: lifespan, routing, OpenAPI, errors │
                       │                                              │
MCP client  ── /mcp ──▶│   mount  ── FastMCP.streamable_http_app()    │   ← MCP SDK
                       │             (JSON-RPC + Streamable HTTP/SSE) │     territory
                       │                                              │
HTTP client ── /api ──▶│   router ── myna.api (health, admin, ...)    │   ← pure FastAPI
                       └──────────────────────────────────────────────┘
                                       │
                              app.state.mcp = FastMCP
                              │
                              ├── tools     (myna.tools.*)
                              ├── resources (myna.resources.*)
                              └── prompts   (myna.prompts.*)
```

The boundary is the `app.mount(...)` call in `src/myna/main.py`:
everything left of the mount is yours (FastAPI), everything right of it
is the SDK's responsibility (MCP wire protocol).

- The MCP app is mounted at `MYNA_MCP_MOUNT_PATH` (default `/mcp`). Its
  internal Streamable HTTP path is set to `/`, so the endpoint is `/mcp/`.
  A request to `/mcp` gets a `307` redirect to `/mcp/`, so configure
  clients with the trailing slash.
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
├── middleware.py       # ASGI auth + rate limiting, scoped to /mcp
├── rate_limit.py       # token bucket
├── cache.py            # @cached TTL decorator
├── context.py          # current_caller ContextVar
├── observability.py    # metrics, audit log, spans for all MCP primitives
├── tracing.py          # OpenTelemetry provider + exporter setup
├── weather_client.py   # Open-Meteo HTTP client
├── api/                # admin REST surface
│   ├── __init__.py     #   /api router aggregation
│   ├── auth.py         #   bearer checks for admin routes and /metrics
│   ├── health.py       #   GET /api/health
│   └── admin.py        #   GET /api/admin/{tools,resources,prompts}
├── tools/              # MCP tool modules (one per topic)
│   ├── example.py      #   ping, echo
│   ├── streaming.py    #   stream_count (Context-streamed)
│   ├── sampling.py     #   summarize_via_sampling (stateful mode only)
│   ├── elicitation.py  #   confirm_action (stateful mode only)
│   └── weather.py      #   get_weather (real, via Open-Meteo)
├── resources/          # MCP resource modules
│   └── example.py      #   myna://server-info, weather://locations/{location}
└── prompts/            # MCP prompt modules
    └── example.py      #   summarize, weather-report
```

## Key design choices

- **FastAPI hosts, SDK speaks MCP.** We use FastAPI for everything we
  want to own (routing, DI, auth, settings, OpenAPI) and delegate only
  the MCP wire protocol to `FastMCP`. This keeps our codebase in
  ordinary FastAPI/Python idioms while staying spec-compliant for free.
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
- **Stateless HTTP by default.** Each request is self-contained, which
  is friendlier to horizontal scaling and load-balancing than
  session-affinity HTTP. See below for what this gives up.

## Stateless and stateful mode

`MYNA_MCP_STATELESS` sets the SDK's `stateless_http` option. It defaults
to `true`.

| | Stateless (default) | Stateful |
| --- | --- | --- |
| Multiple replicas | No session affinity needed | Session affinity needed |
| Tool results and log events | Delivered | Delivered |
| Progress notifications | Dropped | Delivered |
| Sampling and elicitation tools | Not registered | Registered |

Sampling and elicitation send a request to the client in the middle of a
tool call and wait for the reply. In stateless mode that round trip can't
finish and the call hangs, so `build_mcp()` doesn't register those tools.
`tests/test_wire.py` covers both modes over the real transport.
