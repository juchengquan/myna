from __future__ import annotations

import asyncio

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    async def stream_count(
        ctx: Context,
        n: int = 5,
        delay_ms: int = 200,
    ) -> str:
        """Count from 1 to *n*, streaming progress + log events to the client.

        Useful as a dummy long-running tool to verify that progress
        notifications and log messages flow over the Streamable HTTP
        transport. Pure demo — does no real work. MCP logging is deprecated
        as of the 2026-07-28 spec, so new tools should use progress or
        standard logging instead.

        Args:
            n: How high to count (1..20).
            delay_ms: Delay between steps in milliseconds (0..2000).
        """
        if not 1 <= n <= 20:
            raise ToolError("n must be between 1 and 20")
        if not 0 <= delay_ms <= 2000:
            raise ToolError("delay_ms must be between 0 and 2000")

        for i in range(1, n + 1):
            await ctx.report_progress(progress=i, total=n, message=f"step {i}/{n}")
            await ctx.info(f"tick {i}")
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)

        return f"counted to {n}"
