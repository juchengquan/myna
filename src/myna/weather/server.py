from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.server.mcpserver.prompts.base import Message, UserMessage
from pydantic import BaseModel, Field

from myna.cache import cached
from myna.weather.client import (
    LocationNotFound,
    WeatherClient,
    WeatherFetchError,
    condition_from_wmo,
)

Unit = Literal["celsius", "fahrenheit"]


class WeatherReport(BaseModel):
    """Current-weather snapshot for a single location."""

    location: str = Field(description="Resolved place name from the geocoder.")
    country: str | None = None
    latitude: float
    longitude: float
    temperature: float = Field(description="Current temperature in the requested unit.")
    unit: Unit
    condition: str = Field(description="Short human-readable weather condition.")
    humidity_pct: int = Field(ge=0, le=100)
    wind_kph: float
    as_of: str | None = Field(
        default=None,
        description="UTC timestamp the upstream payload was generated.",
    )
    source: str = "open-meteo"


# Module-level client + per-process cache. The client itself is cheap
# to construct (no connections held); we keep one instance so tests can
# swap it via `set_weather_client` without rebuilding the cache layer.
_client: WeatherClient | None = None


def get_weather_client() -> WeatherClient:
    global _client
    if _client is None:
        _client = WeatherClient()
    return _client


def set_weather_client(client: WeatherClient | None) -> None:
    """Override (or clear) the module-level client. Test hook."""
    global _client
    _client = client


@cached(ttl_seconds=60, label="weather_open_meteo")
async def _fetch_celsius(location: str) -> WeatherReport:
    """Cached celsius fetch, shared by the tool and the templated resource.

    Caching here (rather than on `get_weather`) means the unit-conversion
    branch doesn't double the upstream traffic when a client asks for
    both celsius and fahrenheit on the same place.
    """
    payload = await get_weather_client().fetch_current(location)

    return WeatherReport(
        location=payload.resolved_name,
        country=payload.country,
        latitude=payload.latitude,
        longitude=payload.longitude,
        temperature=round(payload.temperature_celsius, 1),
        unit="celsius",
        condition=condition_from_wmo(payload.weather_code),
        humidity_pct=payload.humidity_pct,
        wind_kph=payload.wind_kph,
        as_of=payload.as_of,
    )


async def fetch_weather(location: str, unit: Unit = "celsius") -> WeatherReport:
    """Internal helper shared by the tool and the weather resource."""
    report = await _fetch_celsius(location)
    if unit == "fahrenheit":
        report = report.model_copy(
            update={
                "temperature": round(report.temperature * 9 / 5 + 32, 1),
                "unit": "fahrenheit",
            }
        )
    return report


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    async def get_weather(location: str, unit: Unit = "celsius") -> WeatherReport:
        """Return the current weather for a location, via Open-Meteo.

        Looks up coordinates via Open-Meteo's geocoding API, then fetches
        current temperature, humidity, wind speed, and a coarse weather
        condition derived from the WMO weather code. Results in celsius
        are cached for 60 seconds per location, so repeated calls for the
        same place (in either unit) hit the upstream API at most once
        per minute. See `myna_tool_cache_total{tool="weather_open_meteo"}`
        in Prometheus for hit / miss counts.

        Args:
            location: Free-text place name (e.g. "Tokyo", "Berlin, DE").
            unit: Temperature unit for the returned report.
        """
        if not location.strip():
            raise ToolError("location must not be empty")
        try:
            return await fetch_weather(location, unit)
        except LocationNotFound as exc:
            raise ToolError(str(exc)) from exc
        except WeatherFetchError as exc:
            raise ToolError(f"weather fetch failed: {exc}") from exc

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

    @mcp.prompt(
        name="weather-report",
        description=(
            "Produce a short, human-friendly weather summary for a location, "
            "given a structured report from the `get_weather` tool."
        ),
    )
    def weather_report(location: str, tone: str = "neutral") -> list[Message]:
        return [
            UserMessage(
                f"Use the `get_weather` tool to fetch the current conditions for "
                f"{location!r}, then write a {tone} one-paragraph summary suitable "
                "for a daily briefing. Call out anything unusual (very hot, very "
                "cold, severe weather)."
            )
        ]
