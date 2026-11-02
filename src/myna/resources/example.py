"""Example MCP resources.

Resources are read-only data the client can pull by URI. Two flavors:

- Static — fixed URI, returns the same content (e.g. `myna://server-info`).
- Templated — URI contains `{vars}`; the client fills them in
  (see `weather/server.py`).

Each module exposes a `register(mcp)` function and is listed in
`mcp_server.REGISTRATIONS`.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from myna import __version__
from myna.config import get_settings


def register(mcp: MCPServer) -> None:
    @mcp.resource(
        "myna://server-info",
        name="server-info",
        mime_type="application/json",
        description="Static metadata about this Myna server.",
    )
    def server_info() -> str:
        settings = get_settings()
        payload: dict[str, Any] = {
            "name": settings.mcp_server_name,
            "version": __version__,
            "env": settings.env,
            "mcp_mount_path": settings.mcp_mount_path,
        }
        return json.dumps(payload, indent=2)
