"""Test-only tools, resources, and a prompt.

`conftest.py` adds these to every server the tests build, so tests of the
framework (auth, metrics, tracing, the transport) don't depend on the example
modules, which a new server deletes. Tests of an example module still use that
module.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.mcpserver import Context, Elicit, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

INFO_URI = "probe://info"


class _Answer(BaseModel):
    confirmed: bool


def _ask(action: str) -> Elicit[_Answer]:
    return Elicit(f"Confirm {action}?", _Answer)


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def probe_ping() -> str:
        """Return 'pong'."""
        return "pong"

    @mcp.tool()
    def probe_fail() -> str:
        """Always fail with a tool error."""
        raise ToolError("probe failed")

    @mcp.tool()
    async def probe_count(ctx: Context, n: int = 2) -> str:
        """Report progress from 1 to n."""
        for i in range(1, n + 1):
            await ctx.report_progress(progress=i, total=n)
        return f"counted to {n}"

    @mcp.tool()
    async def probe_confirm(
        action: str, answer: Annotated[ElicitationResult[_Answer], Resolve(_ask)]
    ) -> str:
        """Ask the user to confirm, then report the answer."""
        if isinstance(answer, AcceptedElicitation) and answer.data.confirmed:
            return f"approved {action}"
        return f"not approved {action}"

    @mcp.resource(INFO_URI, name="probe-info")
    def probe_info() -> str:
        return "probe"

    @mcp.prompt(name="probe_prompt")
    def probe_prompt(text: str) -> str:
        return f"Repeat: {text}"
