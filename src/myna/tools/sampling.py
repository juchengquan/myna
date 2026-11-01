"""MCP sampling demo.

A `Sample` returned from a resolver reverses the usual flow: a tool
running on the server asks the *client's* LLM to do work. The SDK picks
how to ask from the negotiated protocol version: a mid-call
`sampling/createMessage` request on a legacy connection, or a question in
the tool result on a 2026-07-28 connection. Useful when:

- The server has structured data the LLM needs to summarize/transform,
  and you want to use the client's existing model rather than bringing
  your own.
- You want to build agentic loops without the server holding API keys
  for a foreign LLM provider.

The standalone sampling feature is deprecated as of the 2026-07-28 spec.
New servers that don't need the client's model should call an LLM
provider directly.

Requires the client to advertise the `sampling` capability. Without it,
the call fails with a `-32021` protocol error.

Only registered when `MYNA_MCP_STATELESS=false`. A legacy client in
stateless mode has no channel to receive the request.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer, Resolve, Sample
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CreateMessageResult, SamplingMessage, TextContent


def _ask_client(text: str, max_words: int = 50) -> Sample:
    if not 1 <= max_words <= 500:
        raise ToolError("max_words must be between 1 and 500")
    if not text.strip():
        raise ToolError("text must not be empty")

    return Sample(
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


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    async def summarize_via_sampling(
        text: str,
        reply: Annotated[CreateMessageResult, Resolve(_ask_client)],
        max_words: int = 50,
    ) -> str:
        """Summarize `text` by asking the *client's* LLM to do it.

        Demonstrates MCP sampling: instead of the server calling an LLM
        directly, it asks the client, which forwards the request to whatever
        model the client has configured. Returns the model's text reply.

        Args:
            text: Content to summarize.
            max_words: Approximate target length of the summary.
        """
        content = reply.content
        if isinstance(content, TextContent):
            return content.text
        # Sampling can in principle return non-text content; surface that
        # honestly rather than silently coercing.
        raise ToolError(f"Sampling returned non-text content of type {type(content).__name__!r}")
