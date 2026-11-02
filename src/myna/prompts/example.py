"""Example MCP prompts.

Prompts are reusable templated message sequences a client (typically an
LLM-driven agent) can fetch and use. The simplest form returns a single
string, which the SDK wraps as a `user` message.

Each module exposes a `register(mcp)` function and is listed in
`mcp_server.REGISTRATIONS`.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    @mcp.prompt(
        name="summarize",
        description="Ask the model to summarize a block of text in N sentences.",
    )
    def summarize(text: str, sentences: int = 3) -> str:
        return (
            f"Please summarize the following text in {sentences} sentence(s). "
            "Be concise and preserve key facts.\n\n"
            f"---\n{text}\n---"
        )
