from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from myna.main import create_app
from myna.tools import weather as weather_tool
from myna.weather_client import WeatherClient


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Open-Meteo mock transport
#
# Tests that exercise the weather tool / resource / cache need a deterministic
# upstream that doesn't touch the network. This fixture installs a
# `httpx.MockTransport` for the duration of one test, clears the cached
# `_fetch_celsius` between tests, and tears everything down on exit.
# ---------------------------------------------------------------------------


def _default_open_meteo_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/v1/search" in url:
        name = request.url.params.get("name", "Unknown")
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": name,
                        "country": "Testland",
                        "latitude": 10.0,
                        "longitude": 20.0,
                    }
                ]
            },
        )
    if "/v1/forecast" in url:
        return httpx.Response(
            200,
            json={
                "current": {
                    "time": "2026-05-21T12:00",
                    "temperature_2m": 21.5,
                    "relative_humidity_2m": 55,
                    "wind_speed_10m": 12.0,
                    "weather_code": 3,
                }
            },
        )
    return httpx.Response(404, json={"error": f"unmocked URL: {url}"})


@pytest.fixture()
def mock_weather() -> Iterator[Callable[[Callable[[httpx.Request], httpx.Response]], None]]:
    """Install a mocked WeatherClient. Yields a setter so tests can swap
    handlers for specific error scenarios."""
    state: dict[str, Any] = {"handler": _default_open_meteo_handler}

    def handler(request: httpx.Request) -> httpx.Response:
        return state["handler"](request)

    transport = httpx.MockTransport(handler)
    weather_tool.set_weather_client(WeatherClient(transport=transport))
    asyncio.get_event_loop().run_until_complete(
        weather_tool._fetch_celsius._myna_cache.clear()  # type: ignore[attr-defined]
    )

    def use(new_handler: Callable[[httpx.Request], httpx.Response]) -> None:
        state["handler"] = new_handler

    try:
        yield use
    finally:
        weather_tool.set_weather_client(None)
        asyncio.get_event_loop().run_until_complete(
            weather_tool._fetch_celsius._myna_cache.clear()  # type: ignore[attr-defined]
        )
