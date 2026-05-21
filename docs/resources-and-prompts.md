# Resources and prompts

The MCP spec defines three primitives: **tools**, **resources**, and
**prompts**. Tools are covered in [tools.md](tools.md); this doc covers
the other two.

## Resources

Read-only data the client can pull by URI. Two flavors:

- **Static** — fixed URI, fixed (or computed-from-server-state) content.
  Example: `myna://server-info`.
- **Templated** — URI contains `{vars}`; the client substitutes them at
  read time. Example: `weather://locations/{location}`.

Resources live under [`src/myna/resources/`](../src/myna/resources/).
Each module exposes a `register(mcp)` function and is wired up from
`mcp_server._register_resources()`.

### Authoring

```python
# src/myna/resources/example.py
from mcp.server.fastmcp import FastMCP

def register(mcp: FastMCP) -> None:
    @mcp.resource(
        "myna://server-info",
        name="server-info",
        mime_type="application/json",
        description="Static metadata about this Myna server.",
    )
    def server_info() -> str:
        return '{"version": "0.1.0"}'

    @mcp.resource(
        "weather://locations/{location}",
        name="weather-by-location",
        mime_type="application/json",
        description="Dummy weather data for a location.",
    )
    def by_location(location: str) -> str:
        return f'{{"location": {location!r}}}'
```

The decorator inspects the function signature: parameters whose names
match URI template variables are bound at read time. The return value
becomes the resource body. Return `bytes` for binary resources.

### Reading from a client

```python
contents = await session.read_resource("weather://locations/Tokyo")
```

### When to use a resource vs a tool

- Use a **resource** when the data is conceptually read-only and
  cacheable. Clients may snapshot resources without LLM involvement.
- Use a **tool** when the operation has side effects, requires the LLM
  to choose arguments, or needs to stream progress.

`get_weather` and `weather://locations/{location}` deliberately expose
the same dummy data both ways as a worked example of the contrast.

## Prompts

Reusable templated message sequences. Clients fetch a prompt by name +
arguments and use the returned messages as a starting point for an LLM
exchange. Prompts have **no side effects** — they just produce text.

Prompts live under [`src/myna/prompts/`](../src/myna/prompts/) and follow
the same `register(mcp)` pattern.

### Authoring

```python
# src/myna/prompts/example.py
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts.base import Message, UserMessage

def register(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="summarize",
        description="Ask the model to summarize text in N sentences.",
    )
    def summarize(text: str, sentences: int = 3) -> str:
        # A bare string is wrapped as a single user message.
        return f"Summarize in {sentences} sentences:\n\n{text}"

    @mcp.prompt(name="weather-report")
    def weather_report(location: str) -> list[Message]:
        # Return a Message sequence for richer control over role/order.
        return [
            UserMessage(
                f"Use the get_weather tool to fetch {location} and write a "
                "one-paragraph summary."
            )
        ]
```

### Fetching from a client

```python
result = await session.get_prompt(
    "summarize",
    {"text": "MCP lets agents call tools.", "sentences": "1"},
)
for msg in result.messages:
    print(msg.role, msg.content.text)
```

> Prompt arguments arrive as strings over the wire (per the MCP spec),
> so even integer-typed parameters should be passed as strings on the
> client. FastMCP coerces them on the server side.

## Observability note

`/metrics` and the audit log currently cover **tool calls only**.
`resources/read` and `prompts/get` are not yet instrumented — separate
follow-up.

## Admin REST endpoints

In addition to `/api/admin/tools`:

- `GET /api/admin/resources` — lists both static resources and
  templates, with an `is_template` flag.
- `GET /api/admin/prompts` — lists prompts with their argument schema.

See [api.md](api.md) for the full reference.
