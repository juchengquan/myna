from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from myna.config import get_settings


def build_mcp() -> FastMCP:
    """Create a new FastMCP instance with all tools registered.

    A fresh instance is required per app lifecycle: FastMCP's
    StreamableHTTPSessionManager.run() can only be invoked once
    per instance.
    """
    settings = get_settings()
    mcp = FastMCP(
        name=settings.mcp_server_name,
        stateless_http=True,
        streamable_http_path="/",
    )
    _register_tools(mcp)
    return mcp


def _register_tools(mcp: FastMCP) -> None:
    from myna.tools import example, streaming, weather

    example.register(mcp)
    streaming.register(mcp)
    weather.register(mcp)
