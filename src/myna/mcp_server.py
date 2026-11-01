from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from myna import __version__
from myna.config import get_settings
from myna.observability import ObservedMCPServer


def build_mcp() -> MCPServer:
    """Create a new MCPServer instance with all tools registered.

    A fresh instance is required per app lifecycle: the session manager's
    run() can only be invoked once per instance.
    """
    settings = get_settings()
    mcp = ObservedMCPServer(settings.mcp_server_name, version=__version__)
    _register_tools(mcp)
    _register_resources(mcp)
    _register_prompts(mcp)
    return mcp


def build_mcp_app(mcp: MCPServer) -> Starlette:
    """Build the ASGI app that serves `mcp` over Streamable HTTP."""
    settings = get_settings()
    return mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=settings.mcp_stateless,
        session_idle_timeout=settings.mcp_session_idle_timeout_seconds or None,
        max_sessions=settings.mcp_max_sessions or None,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.mcp_allowed_origins,
        ),
    )


def _register_tools(mcp: MCPServer) -> None:
    from myna import weather
    from myna.tools import elicitation, example, streaming

    example.register(mcp)
    streaming.register(mcp)
    weather.register(mcp)
    elicitation.register(mcp)


def _register_resources(mcp: MCPServer) -> None:
    from myna.resources import example as resource_example

    resource_example.register(mcp)


def _register_prompts(mcp: MCPServer) -> None:
    from myna.prompts import example as prompt_example

    prompt_example.register(mcp)
