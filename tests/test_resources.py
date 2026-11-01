from __future__ import annotations

import json

import pytest

from myna import __version__
from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_static_resource_registered() -> None:
    mcp = build_mcp()
    statics = await mcp.list_resources()
    assert "server-info" in {r.name for r in statics}


@pytest.mark.asyncio
async def test_server_info_content() -> None:
    mcp = build_mcp()
    contents = await mcp.read_resource("myna://server-info")
    contents_list = list(contents)
    assert len(contents_list) == 1
    payload = json.loads(contents_list[0].content)
    assert payload["version"] == __version__
    assert "name" in payload
