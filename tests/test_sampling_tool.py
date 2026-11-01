from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_sampling_tool_registered_when_stateful() -> None:
    mcp = build_mcp(stateless=False)
    tools = await mcp.list_tools()
    assert "summarize_via_sampling" in {t.name for t in tools}


@pytest.mark.asyncio
async def test_sampling_tool_not_registered_when_stateless() -> None:
    mcp = build_mcp(stateless=True)
    tools = await mcp.list_tools()
    assert "summarize_via_sampling" not in {t.name for t in tools}


@pytest.mark.asyncio
async def test_sampling_rejects_empty_text() -> None:
    # Input validation runs before any session/Context use, so it can be
    # exercised in-process without a live MCP client.
    mcp = build_mcp(stateless=False)
    with pytest.raises(ToolError, match="text must not be empty"):
        await mcp.call_tool("summarize_via_sampling", {"text": "   "})


@pytest.mark.asyncio
async def test_sampling_rejects_out_of_range_max_words() -> None:
    mcp = build_mcp(stateless=False)
    with pytest.raises(ToolError, match="max_words must be between 1 and 500"):
        await mcp.call_tool(
            "summarize_via_sampling", {"text": "hi", "max_words": 0}
        )
