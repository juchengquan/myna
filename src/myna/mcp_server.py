from __future__ import annotations

from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from myna import __version__
from myna.config import get_settings
from myna.observability import ObservedMCPServer
from myna.prompts.example import register as register_example_prompts
from myna.resources.example import register as register_example_resources
from myna.tools.elicitation import register as register_elicitation
from myna.tools.example import register as register_example_tools
from myna.tools.streaming import register as register_streaming
from myna.weather import register as register_weather

# Each module's `register(mcp)` adds its tools, resources, and prompts. To add a
# module, import it and list it here. To remove one, delete both of its lines.
REGISTRATIONS: list[Callable[[MCPServer], None]] = [
    register_example_tools,
    register_streaming,
    register_elicitation,
    register_weather,
    register_example_resources,
    register_example_prompts,
]


def build_mcp() -> MCPServer:
    """Create a new MCPServer instance with all tools registered.

    A fresh instance is required per app lifecycle: the session manager's
    run() can only be invoked once per instance.
    """
    settings = get_settings()
    mcp = ObservedMCPServer(settings.mcp_server_name, version=__version__)
    for register in REGISTRATIONS:
        register(mcp)
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
