from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from myna.config import get_settings
from myna.observability import instrument


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
    instrument(mcp)
    _register_tools(mcp)
    _register_resources(mcp)
    _register_prompts(mcp)
    return mcp


def _register_tools(mcp: FastMCP) -> None:
    from myna.tools import example, streaming, weather

    example.register(mcp)
    streaming.register(mcp)
    weather.register(mcp)


def _register_resources(mcp: FastMCP) -> None:
    from myna.resources import example as resource_example

    resource_example.register(mcp)


def _register_prompts(mcp: FastMCP) -> None:
    from myna.prompts import example as prompt_example

    prompt_example.register(mcp)
