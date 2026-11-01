from __future__ import annotations

from typing import Any

import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_get_weather_registered() -> None:
    mcp = build_mcp()
    tools = await mcp.list_tools()
    assert "get_weather" in {t.name for t in tools}


@pytest.mark.asyncio
async def test_get_weather_returns_expected_payload(mock_weather: Any) -> None:
    mcp = build_mcp()
    body = (await mcp.call_tool("get_weather", {"location": "Tokyo"})).structured_content
    assert body["location"] == "Tokyo"
    assert body["unit"] == "celsius"
    assert body["country"] == "Testland"
    assert body["temperature"] == 21.5
    assert body["humidity_pct"] == 55
    assert body["condition"] == "overcast"  # weather_code 3
    assert body["source"] == "open-meteo"


@pytest.mark.asyncio
async def test_get_weather_respects_unit(mock_weather: Any) -> None:
    mcp = build_mcp()
    args = {"location": "Paris"}
    c = (await mcp.call_tool("get_weather", {**args, "unit": "celsius"})).structured_content
    f = (await mcp.call_tool("get_weather", {**args, "unit": "fahrenheit"})).structured_content
    assert c["unit"] == "celsius"
    assert f["unit"] == "fahrenheit"
    assert f["temperature"] == pytest.approx(c["temperature"] * 9 / 5 + 32, abs=0.1)


@pytest.mark.asyncio
async def test_get_weather_rejects_empty_location(mock_weather: Any) -> None:
    mcp = build_mcp()
    with pytest.raises(Exception, match="location must not be empty"):
        await mcp.call_tool("get_weather", {"location": "   "})


@pytest.mark.asyncio
async def test_get_weather_reports_unknown_location(mock_weather: Any) -> None:
    mock_weather(lambda request: httpx2.Response(200, json={"results": []}))
    mcp = build_mcp()
    with pytest.raises(ToolError, match="Nowhere"):
        await mcp.call_tool("get_weather", {"location": "Nowhere"})


@pytest.mark.asyncio
async def test_get_weather_reports_upstream_failure(mock_weather: Any) -> None:
    mock_weather(lambda request: httpx2.Response(500))
    mcp = build_mcp()
    with pytest.raises(ToolError, match="weather fetch failed"):
        await mcp.call_tool("get_weather", {"location": "Tokyo"})
