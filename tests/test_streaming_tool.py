from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_stream_count_registered() -> None:
    mcp = build_mcp()
    tools = await mcp.list_tools()
    assert "stream_count" in {t.name for t in tools}


@pytest.mark.asyncio
async def test_stream_count_rejects_out_of_range_n() -> None:
    # Input validation runs before any Context access, so we can verify
    # it without a live MCP session.
    mcp = build_mcp()
    with pytest.raises(ToolError, match="n must be between 1 and 20"):
        await mcp.call_tool("stream_count", {"n": 0, "delay_ms": 0})


@pytest.mark.asyncio
async def test_stream_count_rejects_out_of_range_delay() -> None:
    mcp = build_mcp()
    with pytest.raises(ToolError, match="delay_ms must be between 0 and 2000"):
        await mcp.call_tool("stream_count", {"n": 5, "delay_ms": 5000})
