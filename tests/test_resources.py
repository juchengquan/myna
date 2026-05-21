from __future__ import annotations

import json
from typing import Any

import pytest

from myna import __version__
from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_static_resource_registered() -> None:
    mcp = build_mcp()
    statics = await mcp.list_resources()
    assert "server-info" in {r.name for r in statics}


@pytest.mark.asyncio
async def test_resource_template_registered() -> None:
    mcp = build_mcp()
    templates = await mcp.list_resource_templates()
    assert any(
        t.uriTemplate == "weather://locations/{location}" for t in templates
    )


@pytest.mark.asyncio
async def test_server_info_content() -> None:
    mcp = build_mcp()
    contents = await mcp.read_resource("myna://server-info")
    contents_list = list(contents)
    assert len(contents_list) == 1
    payload = json.loads(contents_list[0].content)
    assert payload["version"] == __version__
    assert "name" in payload


@pytest.mark.asyncio
async def test_weather_template_resolves(mock_weather: Any) -> None:
    mcp = build_mcp()
    contents = list(await mcp.read_resource("weather://locations/Tokyo"))
    assert len(contents) == 1
    payload = json.loads(contents[0].content)
    assert payload["location"] == "Tokyo"
    assert payload["unit"] == "celsius"
    # Resource shares the same fetch path as the tool, so the mocked
    # upstream payload flows through unchanged.
    assert payload["country"] == "Testland"
    assert payload["temperature"] == 21.5
