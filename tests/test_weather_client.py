from __future__ import annotations

import httpx
import pytest

from myna.weather_client import (
    LocationNotFound,
    WeatherClient,
    WeatherFetchError,
    condition_from_wmo,
)


def _make_client(handler: httpx.MockTransport | None = None) -> WeatherClient:
    return WeatherClient(
        geocoding_base_url="https://geo.test",
        forecast_base_url="https://api.test",
        timeout_seconds=1.0,
        transport=handler,
    )


def _handler(geo_status: int = 200, geo_body: dict | None = None,
             fc_status: int = 200, fc_body: dict | None = None) -> httpx.MockTransport:
    def h(request: httpx.Request) -> httpx.Response:
        if "/v1/search" in str(request.url):
            return httpx.Response(geo_status, json=geo_body or {})
        if "/v1/forecast" in str(request.url):
            return httpx.Response(fc_status, json=fc_body or {})
        return httpx.Response(404)
    return httpx.MockTransport(h)


@pytest.mark.asyncio
async def test_happy_path_returns_normalized_payload() -> None:
    transport = _handler(
        geo_body={"results": [{"name": "Tokyo", "country": "Japan",
                               "latitude": 35.6, "longitude": 139.7}]},
        fc_body={"current": {"time": "2026-05-21T12:00",
                             "temperature_2m": 23.4,
                             "relative_humidity_2m": 60,
                             "wind_speed_10m": 11.0,
                             "weather_code": 0}},
    )
    client = _make_client(transport)
    payload = await client.fetch_current("Tokyo")
    assert payload.resolved_name == "Tokyo"
    assert payload.country == "Japan"
    assert payload.latitude == 35.6
    assert payload.temperature_celsius == 23.4
    assert payload.humidity_pct == 60
    assert payload.weather_code == 0


@pytest.mark.asyncio
async def test_geocoding_empty_results_raises_location_not_found() -> None:
    transport = _handler(geo_body={"results": []})
    client = _make_client(transport)
    with pytest.raises(LocationNotFound, match="no geocoding match"):
        await client.fetch_current("Atlantis")


@pytest.mark.asyncio
async def test_geocoding_5xx_raises_weather_fetch_error() -> None:
    transport = _handler(geo_status=503)
    client = _make_client(transport)
    with pytest.raises(WeatherFetchError, match="geocoding request failed"):
        await client.fetch_current("Tokyo")


@pytest.mark.asyncio
async def test_forecast_5xx_raises_weather_fetch_error() -> None:
    transport = _handler(
        geo_body={"results": [{"name": "Tokyo", "latitude": 35.6, "longitude": 139.7}]},
        fc_status=502,
    )
    client = _make_client(transport)
    with pytest.raises(WeatherFetchError, match="forecast request failed"):
        await client.fetch_current("Tokyo")


@pytest.mark.asyncio
async def test_forecast_malformed_payload_raises_weather_fetch_error() -> None:
    transport = _handler(
        geo_body={"results": [{"name": "Tokyo", "latitude": 35.6, "longitude": 139.7}]},
        fc_body={"current": {"time": "x"}},  # missing required fields
    )
    client = _make_client(transport)
    with pytest.raises(WeatherFetchError, match="malformed forecast payload"):
        await client.fetch_current("Tokyo")


def test_condition_from_wmo_known_codes() -> None:
    assert condition_from_wmo(0) == "clear"
    assert condition_from_wmo(3) == "overcast"
    assert condition_from_wmo(95) == "thunderstorm"
    assert condition_from_wmo(999) == "unknown"
