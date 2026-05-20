from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def ping() -> str:
        """Health check tool — returns 'pong'."""
        return "pong"

    @mcp.tool()
    def echo(message: str) -> str:
        """Echo back the provided message."""
        return message
