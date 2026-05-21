"""Thin async client for the [Open-Meteo](https://open-meteo.com/) public API.

Open-Meteo is keyless and globally free for non-commercial use. Two
endpoints are wrapped:

- Geocoding (`geocoding-api.open-meteo.com/v1/search`) — name → (lat, lon).
- Forecast  (`api.open-meteo.com/v1/forecast`)         — coords → current weather.

Both URLs are configurable (`MYNA_OPEN_METEO_GEOCODING_URL` /
`MYNA_OPEN_METEO_FORECAST_URL`) so tests can drive an `httpx.MockTransport`
without touching the network. The client itself is intentionally
unopinionated about caching or retries — wrap it with `@cached`
upstream when desired.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from myna.config import get_settings


class CurrentWeather(BaseModel):
    """Typed payload returned by `WeatherClient.fetch_current`."""

    resolved_name: str
    country: str | None
    latitude: float
    longitude: float
    temperature_celsius: float
    humidity_pct: int
    wind_kph: float
    weather_code: int
    as_of: str | None


class LocationNotFound(Exception):
    """Raised when the geocoding endpoint returns no match for a query."""


class WeatherFetchError(Exception):
    """Raised on any upstream failure (network, 4xx/5xx, malformed payload)."""


# WMO weather codes -> short human-readable conditions.
# Spec: https://open-meteo.com/en/docs (search for "weather_code").
_WMO_CONDITIONS = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "rain showers",
    81: "rain showers",
    82: "violent rain showers",
    85: "snow showers",
    86: "snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with hail",
}


def condition_from_wmo(code: int) -> str:
    return _WMO_CONDITIONS.get(code, "unknown")


class WeatherClient:
    """Async wrapper around the two Open-Meteo endpoints we use.

    The `transport` parameter exists so tests can pass an
    `httpx.MockTransport`; production code uses the default network
    transport.
    """

    def __init__(
        self,
        *,
        geocoding_base_url: str | None = None,
        forecast_base_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.geocoding_base_url = geocoding_base_url or settings.open_meteo_geocoding_url
        self.forecast_base_url = forecast_base_url or settings.open_meteo_forecast_url
        self.timeout = timeout_seconds or settings.open_meteo_timeout_seconds
        self._transport = transport

    async def fetch_current(self, location: str) -> CurrentWeather:
        """Resolve `location` and return the current-weather payload.

        Raises `LocationNotFound` if the geocoding endpoint returns no
        results, or `WeatherFetchError` for any other upstream failure.
        """
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self.timeout,
            headers={"User-Agent": "myna/0.1 (https://github.com/juchengquan/myna)"},
        ) as client:
            name, country, lat, lon = await self._geocode(client, location)
            temperature_c, humidity, wind_kph, weather_code, as_of = await self._forecast(
                client, lat, lon
            )
            return CurrentWeather(
                resolved_name=name,
                country=country,
                latitude=lat,
                longitude=lon,
                temperature_celsius=temperature_c,
                humidity_pct=humidity,
                wind_kph=wind_kph,
                weather_code=weather_code,
                as_of=as_of,
            )

    async def _geocode(
        self, client: httpx.AsyncClient, location: str
    ) -> tuple[str, str | None, float, float]:
        try:
            resp = await client.get(
                f"{self.geocoding_base_url}/v1/search",
                params={"name": location, "count": 1, "format": "json"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise WeatherFetchError(f"geocoding request failed: {exc}") from exc

        results = (resp.json() or {}).get("results") or []
        if not results:
            raise LocationNotFound(f"no geocoding match for {location!r}")
        first = results[0]
        return (
            first.get("name", location),
            first.get("country"),
            float(first["latitude"]),
            float(first["longitude"]),
        )

    async def _forecast(
        self, client: httpx.AsyncClient, latitude: float, longitude: float
    ) -> tuple[float, int, float, int, str | None]:
        try:
            resp = await client.get(
                f"{self.forecast_base_url}/v1/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                    "wind_speed_unit": "kmh",
                    "temperature_unit": "celsius",
                    "timezone": "UTC",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise WeatherFetchError(f"forecast request failed: {exc}") from exc

        current = (resp.json() or {}).get("current") or {}
        try:
            return (
                float(current["temperature_2m"]),
                int(round(float(current["relative_humidity_2m"]))),
                float(current["wind_speed_10m"]),
                int(current["weather_code"]),
                current.get("time"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherFetchError(f"malformed forecast payload: {exc}") from exc
