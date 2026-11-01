"""Example MCP resources.

Resources are read-only data the client can pull by URI. Two flavors:

- Static — fixed URI, returns the same content (e.g. `myna://server-info`).
- Templated — URI contains `{vars}`; the client fills them in
  (e.g. `weather://locations/{location}`).

Each module exposes a `register(mcp)` function and is wired up from
`mcp_server._register_resources()`.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError

from myna import __version__
from myna.config import get_settings
from myna.tools.weather import fetch_weather
from myna.weather_client import LocationNotFound, WeatherFetchError


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

    @mcp.resource(
        "weather://locations/{location}",
        name="weather-by-location",
        mime_type="application/json",
        description=(
            "Current weather for a location via Open-Meteo, mirroring the "
            "`get_weather` tool. Use the `get_weather` tool when you need "
            "to control the temperature unit; this resource always returns "
            "celsius. Shares the upstream cache with the tool — repeated "
            "reads for the same place hit the API at most once per minute."
        ),
    )
    async def weather_resource(location: str) -> str:
        try:
            report = await fetch_weather(location)
        except LocationNotFound as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        except WeatherFetchError as exc:
            raise ResourceError(f"weather fetch failed: {exc}") from exc
        return report.model_dump_json(indent=2)

