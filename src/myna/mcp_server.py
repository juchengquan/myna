from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from myna.config import get_settings
from myna.observability import instrument


def build_mcp(*, stateless: bool | None = None) -> FastMCP:
    """Create a new FastMCP instance with all tools registered.

    A fresh instance is required per app lifecycle: FastMCP's
    StreamableHTTPSessionManager.run() can only be invoked once
    per instance.

    `stateless` defaults to the `MYNA_MCP_STATELESS` setting.
    """
    settings = get_settings()
    if stateless is None:
        stateless = settings.mcp_stateless
    mcp = FastMCP(
        name=settings.mcp_server_name,
        stateless_http=stateless,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.mcp_allowed_origins,
        ),
    )
    instrument(mcp)
    _register_tools(mcp, stateless=stateless)
    _register_resources(mcp)
    _register_prompts(mcp)
    return mcp


def _register_tools(mcp: FastMCP, *, stateless: bool) -> None:
    from myna.tools import elicitation, example, sampling, streaming, weather

    example.register(mcp)
    streaming.register(mcp)
    weather.register(mcp)

    # These tools wait on a reply from the client. In stateless mode the
    # request or its reply never gets routed, so the tool call would hang.
    if not stateless:
        elicitation.register(mcp)
        sampling.register(mcp)


def _register_resources(mcp: FastMCP) -> None:
    from myna.resources import example as resource_example

    resource_example.register(mcp)


def _register_prompts(mcp: FastMCP) -> None:
    from myna.prompts import example as prompt_example

    prompt_example.register(mcp)
