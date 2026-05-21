"""Observability for MCP tool calls.

Two surfaces:

1. Prometheus metrics — counter + latency histogram, labelled by tool
   name, caller, and status. Exposed at `/metrics` for a Prometheus
   scraper.
2. Structured audit log — one `tool_call` event per call, with
   caller / tool / status / duration / args fingerprint, emitted via
   structlog so it lands in the JSON log stream.

We instrument by wrapping `FastMCP._tool_manager.call_tool`. That is
private SDK surface but it's the single choke point for tool execution,
so the alternative — wrapping every tool function during registration —
would be both more invasive and easier to forget on a new tool.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.tool_manager import ToolManager
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from myna.context import current_caller
from myna.logging_config import get_logger

_TRACER = trace.get_tracer("myna.mcp")

TOOL_CALLS = Counter(
    "myna_tool_calls_total",
    "Number of MCP tool calls, by tool / caller / status.",
    labelnames=("tool", "caller", "status"),
)

TOOL_DURATION = Histogram(
    "myna_tool_call_duration_seconds",
    "MCP tool call latency in seconds, by tool.",
    labelnames=("tool",),
)

RATE_LIMIT_HITS = Counter(
    "myna_rate_limit_hits_total",
    "Number of MCP requests rejected by the rate limiter, by key kind.",
    labelnames=("key_kind",),
)


def instrument(mcp: FastMCP) -> None:
    """Wrap `mcp._tool_manager.call_tool` to record metrics and audit logs."""
    tool_manager: ToolManager = mcp._tool_manager
    original = tool_manager.call_tool
    log = get_logger("myna.audit")

    async def call_tool(
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
        convert_result: bool = False,
    ) -> Any:
        caller = current_caller.get()
        args_fp = _fingerprint(arguments)
        with _TRACER.start_as_current_span(
            f"mcp.tool.call {name}",
            attributes={
                "mcp.tool.name": name,
                "mcp.caller": caller,
                "mcp.args_fingerprint": args_fp,
            },
        ) as span:
            start = time.monotonic()
            status = "ok"
            try:
                return await original(
                    name,
                    arguments,
                    context=context,
                    convert_result=convert_result,
                )
            except Exception as exc:
                status = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                duration = time.monotonic() - start
                TOOL_DURATION.labels(tool=name).observe(duration)
                TOOL_CALLS.labels(tool=name, caller=caller, status=status).inc()
                span.set_attribute("mcp.status", status)
                span.set_attribute("mcp.duration_ms", round(duration * 1000, 2))
                log.info(
                    "tool_call",
                    tool=name,
                    caller=caller,
                    status=status,
                    duration_ms=round(duration * 1000, 2),
                    args_fingerprint=args_fp,
                )

    tool_manager.call_tool = call_tool  # type: ignore[method-assign]


def _fingerprint(arguments: dict[str, Any]) -> str:
    """Short, stable hash of the arguments — useful for correlating calls
    without leaking PII or secrets into logs."""
    try:
        canonical = json.dumps(arguments, sort_keys=True, default=str)
    except TypeError:
        canonical = repr(arguments)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def render_metrics() -> tuple[bytes, str]:
    """Return the current Prometheus exposition payload + content-type."""
    return generate_latest(), CONTENT_TYPE_LATEST
