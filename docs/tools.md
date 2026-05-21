# Writing MCP tools

Tools live under [`src/myna/tools/`](../src/myna/tools/). Each module
exposes a `register(mcp: FastMCP)` function that adds tools (or
resources/prompts) to the shared `FastMCP` instance, and is wired up
in `src/myna/mcp_server.py::_register_tools`.

## Anatomy of a tool module

```python
# src/myna/tools/greeting.py
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def greet(name: str, formal: bool = False) -> str:
        """Greet someone by name.

        Args:
            name: Person to greet.
            formal: Use a formal greeting if true.
        """
        return f"Good day, {name}." if formal else f"Hi {name}!"
```

Wire it up:

```python
# src/myna/mcp_server.py
def _register_tools(mcp: FastMCP) -> None:
    from myna.tools import example, greeting, weather

    example.register(mcp)
    greeting.register(mcp)
    weather.register(mcp)
```

That's it — the tool is now visible to MCP clients and via
`GET /api/admin/tools`.

## Type signatures and schemas

`FastMCP` derives the tool's JSON Schema from the function signature, so:

- Use **standard Python type hints**. Annotations drive the schema.
- For structured returns, return a `pydantic.BaseModel` — clients will
  receive both a text representation and a `structuredContent` object
  (see [`tools/weather.py`](../src/myna/tools/weather.py) for an example).
- Default values become optional parameters in the schema.
- The **docstring** is the tool description shown to LLMs. Treat it as
  prompt copy — be specific about what the tool does, when to call it,
  and what it returns.

## Async tools

Tools can be `async def` — `FastMCP` will await them. Use this for I/O:

```python
import httpx

def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def fetch_status(url: str) -> int:
        """Return the HTTP status of a GET request to `url`."""
        async with httpx.AsyncClient() as client:
            return (await client.get(url)).status_code
```

## Caching tool results

Idempotent tools (deterministic inputs → deterministic outputs, no
side effects) can be wrapped with `@cached(ttl_seconds=...)` to skip
redundant work for the TTL window. Stack it *inside* `@mcp.tool()`:

```python
from myna.cache import cached
from mcp.server.fastmcp import FastMCP

def register(mcp: FastMCP) -> None:
    @mcp.tool()
    @cached(ttl_seconds=60)
    def lookup(symbol: str) -> dict:
        return _expensive_external_call(symbol)
```

The decorator:

- Keys cache entries by a SHA-256 of the args + kwargs (no PII in keys
  needed — the hash is opaque).
- Records every call as `myna_tool_cache_total{tool, outcome}` where
  `outcome` is `hit` or `miss`.
- Adds an `mcp.cache.outcome` attribute to the active `mcp.tool.call`
  span so you can colour-code traces by cache state.
- Works for both sync and async tool bodies.

`get_weather` already uses this — call it twice with the same
`(location, unit)` within 60s and you'll see one miss followed by
hits, both in `/metrics` and in the trace.

**When NOT to cache:**

- Tools that take a `Context` parameter (sampling, elicitation,
  streaming). Each call has live-session semantics.
- Tools with side effects (writes, sends, mutations).
- Tools whose output depends on hidden state (time, the calling user,
  external data outside the args).

In-memory only; for cache sharing across replicas, swap the in-memory
dict in `src/myna/cache.py` for Redis behind the same `TTLCache`
interface.

## Streaming progress and log events

Long-running tools can stream intermediate updates to the client by
taking a `Context` parameter. The SDK forwards `ctx.report_progress`
and `ctx.info` / `ctx.warning` / `ctx.error` calls as MCP notifications
over the Streamable HTTP transport — clients see them live, before the
final return value.

```python
from typing import Any
from mcp.server.fastmcp import Context, FastMCP

def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def long_task(ctx: Context[Any, Any, Any], items: int) -> str:
        for i in range(1, items + 1):
            await ctx.info(f"processing {i}/{items}")
            await ctx.report_progress(progress=i, total=items)
            ...
        return "done"
```

See [`tools/streaming.py`](../src/myna/tools/streaming.py) for a runnable
example (`stream_count`) and [`scripts/smoke_test.py`](../scripts/smoke_test.py)
for a client that registers `logging_callback` and `progress_callback`
to print the streamed events.

> Tools that declare a `Context` parameter cannot be called in-process
> via `mcp.call_tool(...)` without an active MCP session. For unit
> tests, verify registration and input validation only; cover the
> streaming path through the wire-level smoke test.

## Sampling — asking the client's LLM

A tool can reverse the usual direction and ask the *client's* LLM to do
work mid-execution. This is MCP **sampling** (the wire method is
`sampling/createMessage`). The server holds no model API keys; the
client decides which model to use and how much it costs.

```python
from typing import Any
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import SamplingMessage, TextContent

def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def summarize(ctx: Context[Any, Any, Any], text: str) -> str:
        result = await ctx.session.create_message(
            messages=[
                SamplingMessage(
                    role="user",
                    content=TextContent(type="text", text=f"Summarize: {text}"),
                )
            ],
            max_tokens=256,
            system_prompt="You are a concise summarizer.",
        )
        assert isinstance(result.content, TextContent)
        return result.content.text
```

See [`tools/sampling.py`](../src/myna/tools/sampling.py) for the full
worked example (`summarize_via_sampling`).

**Compatibility caveat:** sampling only works with clients that
advertise the `sampling` capability in their `initialize` handshake.
Claude Desktop and Claude Code do; many smaller SDK-based clients
don't. The example tool surfaces a clear error when the capability is
missing rather than hanging.

## Elicitation — asking the user

Where sampling asks the *client's LLM*, elicitation asks the *user*. The
server pauses the tool, the client renders the prompt with the supplied
schema, and the tool resumes when the user answers (or declines, or
cancels).

Use it when a typed decision shouldn't be made unilaterally by the LLM
— confirming a destructive action, picking between a small set of
options, supplying a value the LLM has no way to know.

```python
from typing import Any
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

class ConfirmAnswer(BaseModel):
    confirmed: bool = Field(description="Approve the action?")
    note: str = Field(default="", description="Optional comment.")

def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def confirm_action(ctx: Context[Any, Any, Any], action: str) -> str:
        result = await ctx.elicit(
            message=f"Please confirm: {action}",
            schema=ConfirmAnswer,
        )
        if result.action == "accept" and result.data:
            return f"approved={result.data.confirmed} note={result.data.note!r}"
        return f"user {result.action}ed"
```

The result has three possible `action` values:

- `accept` — user filled in the schema; `result.data` is populated.
- `decline` — user explicitly said no; `result.data` is `None`.
- `cancel` — user dismissed the prompt without answering.

See [`tools/elicitation.py`](../src/myna/tools/elicitation.py) for the
runnable example (`confirm_action`).

**Compatibility caveat:** same as sampling — the client must advertise
the `elicitation` capability. Modern clients support it; the example
tool surfaces a clear error rather than hanging on a request the
client can't satisfy.

## Errors

Raise normal Python exceptions. They are converted to MCP error responses
by the SDK. Use `ValueError`/`TypeError` for caller mistakes, and let
unexpected exceptions bubble — the FastAPI exception handler logs them
as structured records.

## Testing

In-process unit tests work directly against the `FastMCP` instance —
no HTTP layer required:

```python
# tests/test_my_tool.py
import pytest
from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_my_tool() -> None:
    mcp = build_mcp()
    _, result = await mcp.call_tool("greet", {"name": "Ada"})
    assert result["result"].startswith("Hi Ada")
```

For wire-level verification, use the smoke script described in
[development.md](development.md#end-to-end-smoke-test).
