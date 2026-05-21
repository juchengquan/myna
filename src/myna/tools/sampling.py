"""MCP sampling demo.

`ctx.session.create_message(...)` reverses the usual flow: a tool running
on the server asks the *client's* LLM to do work and waits for the
reply. The MCP spec calls this `sampling/createMessage`. Useful when:

- The server has structured data the LLM needs to summarize/transform,
  and you want to use the client's existing model rather than bringing
  your own.
- You want to build agentic loops without the server holding API keys
  for a foreign LLM provider.

Requires the client to advertise the `sampling` capability during the
`initialize` handshake — Claude Desktop and Claude Code support it;
most basic SDK demos do not. The tool returns a clear error if the
capability is missing.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import SamplingMessage, TextContent


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def summarize_via_sampling(
        ctx: Context[Any, Any, Any],
        text: str,
        max_words: int = 50,
    ) -> str:
        """Summarize `text` by asking the *client's* LLM to do it.

        Demonstrates MCP sampling: instead of the server calling an LLM
        directly, it sends a `sampling/createMessage` request back to the
        client, which forwards it to whatever model the client has
        configured. Returns the model's text reply.

        Args:
            text: Content to summarize.
            max_words: Approximate target length of the summary.
        """
        if not 1 <= max_words <= 500:
            raise ValueError("max_words must be between 1 and 500")
        if not text.strip():
            raise ValueError("text must not be empty")

        try:
            result = await ctx.session.create_message(
                messages=[
                    SamplingMessage(
                        role="user",
                        content=TextContent(
                            type="text",
                            text=(
                                f"Summarize the following text in roughly "
                                f"{max_words} words. Preserve key facts and "
                                "use a neutral tone. Reply with only the "
                                "summary itself — no preamble.\n\n"
                                f"---\n{text}\n---"
                            ),
                        ),
                    )
                ],
                max_tokens=max(64, max_words * 4),
                system_prompt="You are a concise summarizer.",
            )
        except Exception as exc:  # pragma: no cover — depends on client
            raise RuntimeError(
                f"Sampling request failed; the client may not advertise "
                f"the `sampling` capability: {exc}"
            ) from exc

        content = result.content
        if isinstance(content, TextContent):
            return content.text
        # Sampling can in principle return non-text content; surface that
        # honestly rather than silently coercing.
        raise RuntimeError(
            f"Sampling returned non-text content of type {type(content).__name__!r}"
        )
