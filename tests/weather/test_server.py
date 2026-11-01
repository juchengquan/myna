from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError

from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_resource_template_registered() -> None:
    mcp = build_mcp()
    templates = await mcp.list_resource_templates()
    assert any(t.uri_template == "weather://locations/{location}" for t in templates)


def test_admin_lists_template_resource(client: TestClient) -> None:
    resources = client.get("/api/admin/resources").json()["resources"]
    by_name = {r["name"]: r for r in resources}
    assert by_name["weather-by-location"]["is_template"] is True


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


@pytest.mark.asyncio
async def test_weather_template_reports_unknown_location(mock_weather: Any) -> None:
    mock_weather(lambda request: httpx2.Response(200, json={"results": []}))
    mcp = build_mcp()
    with pytest.raises(ResourceNotFoundError, match="Nowhere"):
        await mcp.read_resource("weather://locations/Nowhere")


@pytest.mark.asyncio
async def test_weather_template_reports_upstream_failure(mock_weather: Any) -> None:
    mock_weather(lambda request: httpx2.Response(500))
    mcp = build_mcp()
    with pytest.raises(ResourceError, match="weather fetch failed"):
        await mcp.read_resource("weather://locations/Tokyo")


@pytest.mark.asyncio
async def test_weather_report_prompt_renders_with_location() -> None:
    mcp = build_mcp()
    result = await mcp.get_prompt("weather-report", {"location": "Berlin", "tone": "playful"})
    body = result.messages[0].content.text  # type: ignore[union-attr]
    assert "Berlin" in body
    assert "playful" in body
