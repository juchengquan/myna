from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import Context, FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def stream_count(
        ctx: Context[Any, Any, Any],
        n: int = 5,
        delay_ms: int = 200,
    ) -> str:
        """Count from 1 to *n*, streaming progress + log events to the client.

        Useful as a dummy long-running tool to verify that progress
        notifications and log messages flow over the Streamable HTTP
        transport. Pure demo — does no real work.

        Args:
            n: How high to count (1..20).
            delay_ms: Delay between steps in milliseconds (0..2000).
        """
        if not 1 <= n <= 20:
            raise ValueError("n must be between 1 and 20")
        if not 0 <= delay_ms <= 2000:
            raise ValueError("delay_ms must be between 0 and 2000")

        for i in range(1, n + 1):
            await ctx.report_progress(progress=i, total=n, message=f"step {i}/{n}")
            await ctx.info(f"tick {i}")
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)

        return f"counted to {n}"
