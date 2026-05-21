"""MCP elicitation demo.

`ctx.elicit(message, schema)` reverses the usual flow in a different
way than sampling: the server asks the *user* (via the client UI) for
input mid-execution, then resumes the tool once the answer arrives.
Sampling asks the LLM; elicitation asks the human.

Useful when a tool needs a typed decision the LLM shouldn't make
unilaterally — confirming a destructive action, picking between a few
options, supplying a value the LLM has no way to know.

Requires the client to advertise the `elicitation` capability during
the `initialize` handshake. Modern clients (Claude Desktop / Claude
Code) support it; many smaller SDK demos don't. The tool surfaces a
clear error rather than hanging if the capability is missing.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field


class _ConfirmAnswer(BaseModel):
    """Schema for the user's response to `confirm_action`."""

    confirmed: bool = Field(description="Whether the user approves the action.")
    note: str = Field(default="", description="Optional free-text note from the user.")


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def confirm_action(
        ctx: Context[Any, Any, Any],
        action: str,
    ) -> str:
        """Ask the user to confirm `action` before doing anything.

        Demonstrates MCP elicitation: the server pauses the tool and
        asks the human (via the client UI) for a typed answer. The
        client returns `accept` + the answer, `decline`, or `cancel`.

        Returns a short summary of what happened — no side effects.
        Pure demo of the protocol mechanism.
        """
        if not action.strip():
            raise ValueError("action must not be empty")

        try:
            result = await ctx.elicit(
                message=f"Please confirm: {action}",
                schema=_ConfirmAnswer,
            )
        except Exception as exc:  # pragma: no cover — depends on client
            raise RuntimeError(
                f"Elicitation failed; the client may not advertise the "
                f"`elicitation` capability: {exc}"
            ) from exc

        if result.action == "accept" and result.data is not None:
            verb = "approved" if result.data.confirmed else "declined"
            note = f" (note: {result.data.note!r})" if result.data.note else ""
            return f"User {verb} the action {action!r}{note}."
        if result.action == "decline":
            return f"User declined to answer about {action!r}."
        return f"User cancelled the elicitation about {action!r}."
