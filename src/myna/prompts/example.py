"""Example MCP prompts.

Prompts are reusable templated message sequences a client (typically an
LLM-driven agent) can fetch and use. The simplest form returns a single
string, which the SDK wraps as a `user` message.

Each module exposes a `register(mcp)` function and is wired up from
`mcp_server._register_prompts()`.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts.base import Message, UserMessage


def register(mcp: FastMCP) -> None:
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

    @mcp.prompt(
        name="weather-report",
        description=(
            "Produce a short, human-friendly weather summary for a location, "
            "given a structured report from the `get_weather` tool."
        ),
    )
    def weather_report(location: str, tone: str = "neutral") -> list[Message]:
        return [
            UserMessage(
                f"Use the `get_weather` tool to fetch the current conditions for "
                f"{location!r}, then write a {tone} one-paragraph summary suitable "
                "for a daily briefing. Call out anything unusual (very hot, very "
                "cold, severe weather)."
            )
        ]
