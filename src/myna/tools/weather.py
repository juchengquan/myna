from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from myna.cache import cached
from myna.weather_client import (
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
    try:
        payload = await get_weather_client().fetch_current(location)
    except LocationNotFound as exc:
        raise ValueError(str(exc)) from exc
    except WeatherFetchError as exc:
        raise RuntimeError(f"weather fetch failed: {exc}") from exc

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


def register(mcp: FastMCP) -> None:
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
            raise ValueError("location must not be empty")
        return await fetch_weather(location, unit)
