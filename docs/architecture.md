# Architecture

## TL;DR — FastAPI is the host; the MCP SDK only owns `/mcp`

Myna is a **FastAPI application**. FastAPI owns the process: lifespan,
routing, dependency injection, OpenAPI docs, exception handling, the
admin REST surface, and any future HTTP endpoints you add.

The [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
(`MCPServer`) is used as a **mounted sub-application** for one thing only:
correctly speaking the MCP protocol on the `/mcp` URL. Everything else
— tools as plain Python, your own routes, middleware, auth, settings —
stays in normal FastAPI / Python idioms.

So the layering is:

| Layer | Owned by | Why |
| --- | --- | --- |
| ASGI host, lifespan, routing, OpenAPI, admin API | **FastAPI** | Ergonomic, familiar, full control |
| MCP wire protocol on `/mcp` (JSON-RPC framing, Streamable HTTP + SSE, session handling, `initialize`/`tools/list`/`tools/call`, schema generation from type hints) | **`MCPServer`** (mounted) | Spec-compliant, tracks MCP revisions for us |
| Tool implementations | **Plain Python** in `myna/tools/*` | No SDK lock-in beyond the `@mcp.tool()` decorator |

We use the SDK rather than reimplementing MCP as FastAPI routes because
the wire protocol is non-trivial (JSON-RPC, SSE streaming,
`Mcp-Session-Id` semantics, capability negotiation) and the spec is
still evolving. Mounting keeps our surface area small.

## Surfaces

The single ASGI process exposes two surfaces, sharing config, logging,
and lifespan:

1. **MCP server** at `/mcp` — handled by the mounted `MCPServer` sub-app
   (Streamable HTTP transport).
2. **Management/admin REST API** at `/api` — pure FastAPI routes:
   health checks and tool introspection.

## Request flow

```
                       ┌──────────────────────────────────────────────┐
                       │          FastAPI app  (myna.main)            │
                       │  ── owns: lifespan, routing, OpenAPI, errors │
                       │                                              │
MCP client  ── /mcp ──▶│   mount  ── MCPServer.streamable_http_app()    │   ← MCP SDK
                       │             (JSON-RPC + Streamable HTTP/SSE) │     territory
                       │                                              │
HTTP client ── /api ──▶│   router ── myna.api (health, admin, ...)    │   ← pure FastAPI
                       └──────────────────────────────────────────────┘
                                       │
                              app.state.mcp = MCPServer
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
  `MountPathMiddleware` serves `/mcp` the same way, without a redirect.
- The MCP endpoint only accepts the hosts in `MYNA_MCP_ALLOWED_HOSTS`, which
  defaults to localhost. This protects local servers from DNS rebinding, so
  remote deployments must add their hostname or every request gets a `421`.
- The MCPServer `session_manager` is started/stopped by FastAPI's
  `lifespan` context manager — see `src/myna/main.py`.
- A fresh `MCPServer` instance is built per app (`build_mcp()` in
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
├── verifiers.py        # turns a bearer token into the caller
├── rate_limit.py       # token bucket
├── cache.py            # @cached TTL decorator
├── context.py          # current_caller ContextVar
├── observability.py    # metrics, audit log, spans for all MCP primitives
├── tracing.py          # OpenTelemetry provider + exporter setup
├── api/                # admin REST surface
│   ├── __init__.py     #   /api router aggregation
│   ├── auth.py         #   bearer checks for admin routes and /metrics
│   ├── health.py       #   GET /api/health
│   └── admin.py        #   GET /api/admin/{tools,resources,prompts}
├── tools/              # MCP tool modules (one per topic)
│   ├── example.py      #   ping, echo
│   ├── streaming.py    #   stream_count (Context-streamed)
│   └── elicitation.py  #   confirm_action
├── resources/          # MCP resource modules
│   └── example.py      #   myna://server-info
├── prompts/            # MCP prompt modules
│   └── example.py      #   summarize
└── weather/            # example integration, removable as one unit
    ├── client.py       #   Open-Meteo HTTP client and its settings
    └── server.py       #   get_weather, weather://locations/{location}, weather-report
```

## Key design choices

- **FastAPI hosts, SDK speaks MCP.** We use FastAPI for everything we
  want to own (routing, DI, auth, settings, OpenAPI) and delegate only
  the MCP wire protocol to `MCPServer`. This keeps our codebase in
  ordinary FastAPI/Python idioms while staying spec-compliant for free.
- **Mount, don't fork.** MCPServer is mounted into FastAPI rather than run
  as a separate process, so the admin API and the MCP server share state
  (`app.state.mcp`) and are deployed as one unit.
- **Per-app MCP instance.** Required by the SDK's session manager
  lifecycle; also makes `TestClient` and multi-worker setups predictable.
- **Pluggable tool registration.** Each `tools/<name>.py` exposes a
  `register(mcp: MCPServer)` function called from
  `mcp_server._register_tools()`. Adding a tool means adding a module
  and one line in that registrar — no decorator magic at import time,
  so test isolation stays clean.
- **Observe through the public API.** `ObservedMCPServer` overrides the
  server's public `call_tool`, `read_resource`, and `get_prompt`, which the
  protocol handlers also call, so there's no patching of SDK internals.
- **Stateless HTTP by default.** Each request is self-contained, which
  is friendlier to horizontal scaling and load-balancing than
  session-affinity HTTP. See below for what this gives up.

## Stateless and stateful mode

`MYNA_MCP_STATELESS` sets the SDK's `stateless_http` option. It defaults
to `true`, and it only affects legacy clients, which connect with the
`initialize` handshake used up to spec 2025-11-25. The SDK routes each
request by its protocol version, so one endpoint serves both eras.
2026-07-28 clients never have a session.

| | Legacy client, stateless (default) | Legacy client, stateful | 2026-07-28 client |
| --- | --- | --- | --- |
| Multiple replicas | No session affinity needed | Session affinity needed | No session affinity needed |
| Tool results | Delivered | Delivered | Delivered |
| Progress notifications | Delivered | Delivered | Delivered |
| `confirm_action` | Protocol error | Works | Works |

`confirm_action` asks the client a question. It uses a resolver, so the SDK
picks how to ask for each connection: a request mid-call for a legacy client,
or a question in the tool result for a 2026-07-28 client. A legacy client in
stateless mode has no channel for that, so it gets a protocol error.

In stateful mode, a legacy session closes after 30 idle minutes, and new
ones are refused past 10,000 per process, with `503`. Change these with
`MYNA_MCP_SESSION_IDLE_TIMEOUT_SECONDS` and `MYNA_MCP_MAX_SESSIONS`. `tests/test_wire.py` covers both modes and both eras over the
real transport.
