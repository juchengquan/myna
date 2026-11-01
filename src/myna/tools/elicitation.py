"""MCP elicitation demo.

An `Elicit` returned from a resolver reverses the usual flow: the server
asks the *user* (via the client UI) for input, then runs the tool with the
answer. The SDK picks how to ask from the negotiated protocol version, so
one tool body serves both.

Useful when a tool needs a typed decision the LLM shouldn't make
unilaterally — confirming a destructive action, picking between a few
options, supplying a value the LLM has no way to know.

Requires the client to advertise the `elicitation` capability. Without
it, the call fails with a `-32021` protocol error.

A legacy client in stateless mode has no channel to receive the question,
so it gets a protocol error. 2026-07-28 clients work in either server mode.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.elicitation import AcceptedElicitation, DeclinedElicitation, ElicitationResult
from mcp.server.mcpserver import Elicit, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field


class _ConfirmAnswer(BaseModel):
    """Schema for the user's response to `confirm_action`."""

    confirmed: bool = Field(description="Whether the user approves the action.")
    note: str = Field(default="", description="Optional free-text note from the user.")


def _ask_user(action: str) -> Elicit[_ConfirmAnswer]:
    if not action.strip():
        raise ToolError("action must not be empty")
    return Elicit(f"Please confirm: {action}", _ConfirmAnswer)


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    async def confirm_action(
        action: str,
        answer: Annotated[ElicitationResult[_ConfirmAnswer], Resolve(_ask_user)],
    ) -> str:
        """Ask the user to confirm `action` before doing anything.

        Demonstrates MCP elicitation: the server asks the human (via the
        client UI) for a typed answer. The client returns `accept` + the
        answer, `decline`, or `cancel`.

        Returns a short summary of what happened — no side effects.
        Pure demo of the protocol mechanism.
        """
        if isinstance(answer, AcceptedElicitation):
            verb = "approved" if answer.data.confirmed else "declined"
            note = f" (note: {answer.data.note!r})" if answer.data.note else ""
            return f"User {verb} the action {action!r}{note}."
        if isinstance(answer, DeclinedElicitation):
            return f"User declined to answer about {action!r}."
        return f"User cancelled the elicitation about {action!r}."
