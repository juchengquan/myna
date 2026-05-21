from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
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


HandlerSetter = Callable[[Callable[[httpx.Request], httpx.Response]], None]


@pytest.fixture()
async def mock_weather() -> AsyncIterator[HandlerSetter]:
    """Install a mocked WeatherClient. Yields a setter so tests can swap
    handlers for specific error scenarios. Async because clearing the
    shared `_fetch_celsius` cache between tests requires an active event
    loop — pytest-asyncio (configured `asyncio_mode = "auto"`) gives us
    one for free here, and using `asyncio.get_event_loop()` directly
    triggers a DeprecationWarning on Python 3.10+ outside a running loop.
    """
    state: dict[str, Any] = {"handler": _default_open_meteo_handler}

    def handler(request: httpx.Request) -> httpx.Response:
        return state["handler"](request)

    transport = httpx.MockTransport(handler)
    weather_tool.set_weather_client(WeatherClient(transport=transport))
    await weather_tool._fetch_celsius._myna_cache.clear()  # type: ignore[attr-defined]

    def use(new_handler: Callable[[httpx.Request], httpx.Response]) -> None:
        state["handler"] = new_handler

    try:
        yield use
    finally:
        weather_tool.set_weather_client(None)
        await weather_tool._fetch_celsius._myna_cache.clear()  # type: ignore[attr-defined]
