"""End-to-end smoke test for a running Myna MCP server.

Connects to the Streamable HTTP endpoint as a real MCP client, lists
the registered tools, and calls each of the example tools. Use this
to verify the wire protocol is working — `pytest` exercises tools
in-process and won't catch transport-level regressions.

Usage:
    # In one shell, start the server:
    uv run myna

    # In another shell:
    uv run python scripts/smoke_test.py
    uv run python scripts/smoke_test.py --url http://localhost:8000/mcp/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import LoggingMessageNotificationParams


def _dump(label: str, value: Any) -> None:
    print(f"\n=== {label} ===")
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, indent=2, default=str))


async def _on_log(params: LoggingMessageNotificationParams) -> None:
    print(f"  [log/{params.level}] {params.data}")


async def _on_progress(progress: float, total: float | None, message: str | None) -> None:
    bar = ""
    if total:
        pct = int(100 * progress / total)
        bar = f" [{pct:>3}%]"
    print(f"  [progress] {progress}/{total or '?'}{bar}  {message or ''}")


async def run(url: str, api_key: str | None = None) -> int:
    print(f"Connecting to MCP server at {url} ...")
    headers: dict[str, str] | None = None
    if api_key:
        headers = {"Authorization": f"Bearer {api_key}"}
    async with (
        streamablehttp_client(url, headers=headers) as (read, write, _),
        ClientSession(read, write, logging_callback=_on_log) as session,
    ):
        init = await session.initialize()
        _dump("initialize", init)

        tools = await session.list_tools()
        tool_names = [t.name for t in tools.tools]
        _dump("tools/list", {"tools": tool_names})

        expected = {"ping", "echo", "get_weather", "stream_count"}
        missing = expected - set(tool_names)
        if missing:
            print(f"\nMISSING expected tools: {sorted(missing)}", file=sys.stderr)
            return 1

        _dump("call ping", await session.call_tool("ping", {}))
        _dump("call echo", await session.call_tool("echo", {"message": "hello myna"}))
        _dump(
            "call get_weather (Tokyo, celsius)",
            await session.call_tool("get_weather", {"location": "Tokyo"}),
        )
        _dump(
            "call get_weather (Tokyo, fahrenheit)",
            await session.call_tool(
                "get_weather", {"location": "Tokyo", "unit": "fahrenheit"}
            ),
        )

        print("\n=== call stream_count (streaming progress + logs below) ===")
        stream_result = await session.call_tool(
            "stream_count",
            {"n": 5, "delay_ms": 100},
            progress_callback=_on_progress,
        )
        _dump("stream_count final", stream_result)

        # --- Resources ---
        resources = await session.list_resources()
        templates = await session.list_resource_templates()
        _dump(
            "resources/list",
            {
                "resources": [r.name for r in resources.resources],
                "templates": [t.uriTemplate for t in templates.resourceTemplates],
            },
        )

        expected_resources = {"server-info"}
        missing_r = expected_resources - {r.name for r in resources.resources}
        if missing_r:
            print(f"\nMISSING expected resources: {sorted(missing_r)}", file=sys.stderr)
            return 1

        _dump("read myna://server-info", await session.read_resource("myna://server-info"))
        _dump(
            "read weather://locations/Tokyo",
            await session.read_resource("weather://locations/Tokyo"),
        )

        # --- Prompts ---
        prompts = await session.list_prompts()
        _dump("prompts/list", {"prompts": [p.name for p in prompts.prompts]})

        expected_prompts = {"summarize", "weather-report"}
        missing_p = expected_prompts - {p.name for p in prompts.prompts}
        if missing_p:
            print(f"\nMISSING expected prompts: {sorted(missing_p)}", file=sys.stderr)
            return 1

        _dump(
            "get_prompt summarize",
            await session.get_prompt(
                "summarize", {"text": "MCP lets agents call tools.", "sentences": "1"}
            ),
        )
        _dump(
            "get_prompt weather-report",
            await session.get_prompt(
                "weather-report", {"location": "Berlin", "tone": "playful"}
            ),
        )

    print("\nSmoke test OK")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="http://localhost:8000/mcp/",
        help="Streamable HTTP MCP endpoint (default: %(default)s)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Bearer token to send (matches one entry in MYNA_MCP_API_KEYS).",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.url, args.api_key)))


if __name__ == "__main__":
    main()
