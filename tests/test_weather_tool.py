from __future__ import annotations

import pytest

from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_get_weather_registered() -> None:
    mcp = build_mcp()
    tools = await mcp.list_tools()
    assert "get_weather" in {t.name for t in tools}


@pytest.mark.asyncio
async def test_get_weather_is_deterministic() -> None:
    mcp = build_mcp()

    a = await mcp.call_tool("get_weather", {"location": "Tokyo"})
    b = await mcp.call_tool("get_weather", {"location": "Tokyo"})
    assert a == b


@pytest.mark.asyncio
async def test_get_weather_respects_unit() -> None:
    mcp = build_mcp()
    _, c = await mcp.call_tool("get_weather", {"location": "Paris", "unit": "celsius"})
    _, f = await mcp.call_tool("get_weather", {"location": "Paris", "unit": "fahrenheit"})
    assert c["unit"] == "celsius"
    assert f["unit"] == "fahrenheit"
    assert f["temperature"] == pytest.approx(c["temperature"] * 9 / 5 + 32, abs=0.1)
