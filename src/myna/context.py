"""ContextVars carrying per-request state shared across the app.

Currently exposes the caller identity that the MCP auth middleware sets
on each `/mcp` request, so the tool-call observer can attribute audit
log lines and Prometheus metrics to the right client.
"""

from __future__ import annotations

from contextvars import ContextVar

ANONYMOUS = "anonymous"

current_caller: ContextVar[str] = ContextVar("current_caller", default=ANONYMOUS)
