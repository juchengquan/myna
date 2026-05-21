from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_elicitation_tool_registered() -> None:
    mcp = build_mcp()
    tools = await mcp.list_tools()
    assert "confirm_action" in {t.name for t in tools}


@pytest.mark.asyncio
async def test_elicitation_rejects_empty_action() -> None:
    # Input validation fires before ctx.elicit is reached, so the
    # bad-input path is exercisable in-process without a live client.
    mcp = build_mcp()
    with pytest.raises(ToolError, match="action must not be empty"):
        await mcp.call_tool("confirm_action", {"action": "   "})
