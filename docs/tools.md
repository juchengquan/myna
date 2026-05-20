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
