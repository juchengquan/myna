"""End-to-end smoke test for a running Myna MCP server.

Connects to the Streamable HTTP endpoint as a real MCP client, once as a
legacy client and once as a 2026-07-28 client. It lists the registered
tools and calls each of the example tools. Use this to verify the wire
protocol is working — most of `pytest` exercises tools in-process and
won't catch transport-level regressions.

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

import httpx2
from mcp import ClientSession
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST, ElicitResult

CONFIRM_ARGUMENTS = {"action": "run the smoke test"}
INTERACTIVE_TIMEOUT_SECONDS = 15.0
MODERN_PROTOCOL = "2026-07-28"
_TIMEOUT = httpx2.Timeout(30.0, read=300.0)


def _dump(label: str, value: Any) -> None:
    print(f"\n=== {label} ===")
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, indent=2, default=str))


async def _on_progress(progress: float, total: float | None, message: str | None) -> None:
    bar = ""
    if total:
        pct = int(100 * progress / total)
        bar = f" [{pct:>3}%]"
    print(f"  [progress] {progress}/{total or '?'}{bar}  {message or ''}")


async def _on_elicitation(context: Any, params: Any) -> ElicitResult:
    print("  [elicitation] replying as the user")
    return ElicitResult(action="accept", content={"confirmed": True, "note": "smoke test"})


async def run(url: str, api_key: str | None = None) -> int:
    print(f"Connecting to MCP server at {url} ...")
    headers: dict[str, str] | None = None
    if api_key:
        headers = {"Authorization": f"Bearer {api_key}"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=_TIMEOUT) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write),
        ClientSession(read, write, elicitation_callback=_on_elicitation) as session,
    ):
        init = await session.initialize()
        _dump("initialize", init)

        tools = await session.list_tools()
        tool_names = [t.name for t in tools.tools]
        _dump("tools/list", {"tools": tool_names})

        expected = {"ping", "echo", "stream_count", "confirm_action"}
        missing = expected - set(tool_names)
        if missing:
            print(f"\nMISSING expected tools: {sorted(missing)}", file=sys.stderr)
            return 1

        _dump("call ping", await session.call_tool("ping", {}))
        _dump("call echo", await session.call_tool("echo", {"message": "hello myna"}))
        has_weather = "get_weather" in tool_names
        if has_weather:
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

        progress_events = 0

        async def count_progress(progress: float, total: float | None, message: str | None) -> None:
            nonlocal progress_events
            progress_events += 1
            await _on_progress(progress, total, message)

        print("\n=== call stream_count (streaming progress below) ===")
        stream_result = await session.call_tool(
            "stream_count",
            {"n": 5, "delay_ms": 100},
            progress_callback=count_progress,
        )
        _dump("stream_count final", stream_result)

        if progress_events != 5:
            print(f"\nExpected 5 progress events, got {progress_events}", file=sys.stderr)
            return 1

        # A legacy client in stateless mode has no channel for the question.
        try:
            async with asyncio.timeout(INTERACTIVE_TIMEOUT_SECONDS):
                result = await session.call_tool("confirm_action", CONFIRM_ARGUMENTS)
        except TimeoutError:
            print("\nconfirm_action did not finish in time", file=sys.stderr)
            return 1
        except MCPError as exc:
            if exc.error.code != INVALID_REQUEST:
                print(f"\nconfirm_action failed: {exc}", file=sys.stderr)
                return 1
            print("\nServer is stateless: legacy clients can't be asked questions.")
        else:
            _dump("call confirm_action", result)
            if result.is_error:
                print("\nconfirm_action returned an error", file=sys.stderr)
                return 1

        # --- Resources ---
        resources = await session.list_resources()
        templates = await session.list_resource_templates()
        _dump(
            "resources/list",
            {
                "resources": [r.name for r in resources.resources],
                "templates": [t.uri_template for t in templates.resource_templates],
            },
        )

        expected_resources = {"server-info"}
        missing_r = expected_resources - {r.name for r in resources.resources}
        if missing_r:
            print(f"\nMISSING expected resources: {sorted(missing_r)}", file=sys.stderr)
            return 1

        _dump("read myna://server-info", await session.read_resource("myna://server-info"))
        if has_weather:
            _dump(
                "read weather://locations/Tokyo",
                await session.read_resource("weather://locations/Tokyo"),
            )

        # --- Prompts ---
        prompts = await session.list_prompts()
        _dump("prompts/list", {"prompts": [p.name for p in prompts.prompts]})

        expected_prompts = {"summarize"}
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
        if has_weather:
            _dump(
                "get_prompt weather-report",
                await session.get_prompt(
                    "weather-report", {"location": "Berlin", "tone": "playful"}
                ),
            )

    return await run_modern(url, headers)


async def run_modern(url: str, headers: dict[str, str] | None) -> int:
    print("\n=== 2026-07-28 client ===")
    progress_events = 0

    async def count_progress(progress: float, total: float | None, message: str | None) -> None:
        nonlocal progress_events
        progress_events += 1

    async with (
        httpx2.AsyncClient(headers=headers, timeout=_TIMEOUT) as http_client,
        Client(
            streamable_http_client(url, http_client=http_client),
            elicitation_callback=_on_elicitation,
        ) as client,
    ):
        print(f"protocol version: {client.protocol_version}")
        if client.protocol_version != MODERN_PROTOCOL:
            print(f"\nExpected protocol {MODERN_PROTOCOL}", file=sys.stderr)
            return 1

        _dump("call ping", await client.call_tool("ping", {}))
        await client.call_tool(
            "stream_count", {"n": 5, "delay_ms": 0}, progress_callback=count_progress
        )
        if progress_events != 5:
            print(f"\nExpected 5 progress events, got {progress_events}", file=sys.stderr)
            return 1

        async with asyncio.timeout(INTERACTIVE_TIMEOUT_SECONDS):
            result = await client.call_tool("confirm_action", CONFIRM_ARGUMENTS)
        _dump("call confirm_action", result)
        if result.is_error:
            print("\nconfirm_action returned an error", file=sys.stderr)
            return 1

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
