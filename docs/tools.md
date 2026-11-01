# Writing MCP tools

Tools live under [`src/myna/tools/`](../src/myna/tools/). Each module
exposes a `register(mcp: MCPServer)` function that adds tools (or
resources/prompts) to the shared `MCPServer` instance, and is wired up
in `src/myna/mcp_server.py::_register_tools`.

## Anatomy of a tool module

```python
# src/myna/tools/greeting.py
from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
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
def _register_tools(mcp: MCPServer) -> None:
    from myna.tools import example, greeting, weather

    example.register(mcp)
    greeting.register(mcp)
    weather.register(mcp)
```

That's it — the tool is now visible to MCP clients and via
`GET /api/admin/tools`.

## Type signatures and schemas

`MCPServer` derives the tool's JSON Schema from the function signature, so:

- Use **standard Python type hints**. Annotations drive the schema.
- For structured returns, return a `pydantic.BaseModel` — clients will
  receive both a text representation and a `structuredContent` object
  (see [`tools/weather.py`](../src/myna/tools/weather.py) for an example).
- Default values become optional parameters in the schema.
- The **docstring** is the tool description shown to LLMs. Treat it as
  prompt copy — be specific about what the tool does, when to call it,
  and what it returns.

## Async tools

Tools can be `async def` — `MCPServer` will await them. Use this for I/O:

```python
import httpx

def register(mcp: MCPServer) -> None:
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
from mcp.server.mcpserver import MCPServer

def register(mcp: MCPServer) -> None:
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

Each cache holds at most `max_entries` entries (default 1024) and evicts
the oldest entry when full.

`get_weather` uses this on its internal `_fetch_celsius(location)` helper,
which is shared with the weather resource and labelled
`weather_open_meteo`. Call it twice for the same location within 60s, in
either unit, and you'll see one miss followed by a hit.

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

These work in both server modes. A 2026-07-28 client only gets log events
if it asks for them with a log level.

MCP logging is deprecated as of the 2026-07-28 spec, so `ctx.info` and
friends emit `MCPDeprecationWarning`. New tools should prefer
`ctx.report_progress` and standard logging.

```python
from mcp.server.mcpserver import Context, MCPServer

def register(mcp: MCPServer) -> None:
    @mcp.tool()
    async def long_task(ctx: Context, items: int) -> str:
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
> streaming path in `tests/test_wire.py`, which uses the real transport.

## Sampling — asking the client's LLM

A tool can reverse the usual direction and ask the *client's* LLM to do
work. This is MCP **sampling**. The server holds no model API keys; the
client decides which model to use and how much it costs.

Declare the question with a resolver. The SDK picks how to ask from the
negotiated protocol version, so one tool body serves both protocol eras.
`ctx.session.create_message` only works on legacy connections.

```python
from typing import Annotated
from mcp.server.mcpserver import MCPServer, Resolve, Sample
from mcp.types import CreateMessageResult, SamplingMessage, TextContent

def ask_client(text: str) -> Sample:
    return Sample(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(type="text", text=f"Summarize: {text}"),
            )
        ],
        max_tokens=256,
        system_prompt="You are a concise summarizer.",
    )

def register(mcp: MCPServer) -> None:
    @mcp.tool()
    async def summarize(
        text: str, reply: Annotated[CreateMessageResult, Resolve(ask_client)]
    ) -> str:
        assert isinstance(reply.content, TextContent)
        return reply.content.text
```

The resolved parameter is invisible to the model and can't be supplied by
a client. On a 2026-07-28 connection a resolver can run again each time
the call resumes, so build the question deterministically from the tool's
arguments.

See [`tools/sampling.py`](../src/myna/tools/sampling.py) for the full
worked example (`summarize_via_sampling`).

**Mode requirement:** a legacy client needs `MYNA_MCP_STATELESS=false`.
In stateless mode the call fails with a protocol error, so Myna registers
these tools only in stateful mode, as `mcp_server._register_tools` does.

**Compatibility caveat:** sampling only works with clients that advertise
the `sampling` capability. Without it, the call fails with a `-32021`
protocol error.

**Deprecation:** the standalone sampling feature is deprecated as of the
2026-07-28 spec. Servers that don't need the client's model should call
an LLM provider directly.

## Elicitation — asking the user

Where sampling asks the *client's LLM*, elicitation asks the *user*. The
client renders the prompt with the supplied schema, and the tool runs
once the user answers (or declines, or cancels).

Use it when a typed decision shouldn't be made unilaterally by the LLM
— confirming a destructive action, picking between a small set of
options, supplying a value the LLM has no way to know.

```python
from typing import Annotated
from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.mcpserver import Elicit, MCPServer, Resolve
from pydantic import BaseModel, Field

class ConfirmAnswer(BaseModel):
    confirmed: bool = Field(description="Approve the action?")
    note: str = Field(default="", description="Optional comment.")

def ask_user(action: str) -> Elicit[ConfirmAnswer]:
    return Elicit(f"Please confirm: {action}", ConfirmAnswer)

def register(mcp: MCPServer) -> None:
    @mcp.tool()
    async def confirm_action(
        action: str,
        answer: Annotated[ElicitationResult[ConfirmAnswer], Resolve(ask_user)],
    ) -> str:
        if isinstance(answer, AcceptedElicitation):
            return f"approved={answer.data.confirmed} note={answer.data.note!r}"
        return f"user {answer.action}ed"
```

The answer has three possible `action` values:

- `accept` — user filled in the schema; `answer.data` is populated.
- `decline` — user explicitly said no.
- `cancel` — user dismissed the prompt without answering.

Annotate the parameter as `ConfirmAnswer` instead if the tool should only
run when the user accepts.

See [`tools/elicitation.py`](../src/myna/tools/elicitation.py) for the
runnable example (`confirm_action`).

**Mode and compatibility:** same as sampling. A legacy client needs
`MYNA_MCP_STATELESS=false`, and the client must advertise the
`elicitation` capability or the call fails with a `-32021` protocol error.

## Errors

Raise `ToolError` (from `mcp.server.mcpserver.exceptions`) for anything
the caller should read, such as a bad argument or an upstream failure. The
client gets your message as an error result.

Any other exception is logged on the server, and the client only sees
`Error executing tool <name>`. Let unexpected exceptions bubble, and don't
rely on `ValueError` to carry a message to the client.

## Testing

In-process unit tests work directly against the `MCPServer` instance —
no HTTP layer required:

```python
# tests/test_my_tool.py
import pytest
from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_my_tool() -> None:
    mcp = build_mcp()
    result = await mcp.call_tool("greet", {"name": "Ada"})
    assert result.structured_content["result"].startswith("Hi Ada")
```

For wire-level coverage, add a case to `tests/test_wire.py`. To check a
running server, use the smoke script described in
[development.md](development.md#end-to-end-smoke-test).
