# Connecting MCP clients

Myna speaks the [Model Context Protocol](https://modelcontextprotocol.io/)
over **Streamable HTTP** at the URL set by `MYNA_MCP_MOUNT_PATH`
(default `/mcp`). The endpoint URL clients should use is e.g.
`http://localhost:8000/mcp/`.

## Python (official SDK)

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    async with (
        streamablehttp_client("http://localhost:8000/mcp/") as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        tools = await session.list_tools()
        print([t.name for t in tools.tools])

        result = await session.call_tool("get_weather", {"location": "Berlin"})
        print(result.structuredContent)


asyncio.run(main())
```

A ready-to-run version of this is at
[`scripts/smoke_test.py`](../scripts/smoke_test.py).

## Claude Code / Claude Desktop

These clients are typically configured to launch local stdio MCP servers.
Myna is HTTP-only by default, so configure it as a remote/HTTP server.
The exact JSON config varies by client version — check the client's
docs for "Streamable HTTP MCP server" or "remote MCP server", and point
it at `http://<host>:<port>/mcp/`.

## TypeScript / Node (`@modelcontextprotocol/sdk`)

```ts
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const transport = new StreamableHTTPClientTransport(
  new URL("http://localhost:8000/mcp/"),
);
const client = new Client({ name: "demo", version: "0.1.0" }, { capabilities: {} });

await client.connect(transport);
const tools = await client.listTools();
console.log(tools.tools.map((t) => t.name));

const result = await client.callTool({
  name: "get_weather",
  arguments: { location: "Berlin" },
});
console.log(result);

await client.close();
```

## Notes

- The MCP endpoint expects `POST` with
  `Accept: application/json, text/event-stream`. Plain `GET /mcp/` will
  not return useful output — use a real MCP client.
- Myna runs in `stateless_http=True` mode: clients should not assume a
  long-lived session and should re-initialize on reconnect.
- For production deployments behind a reverse proxy, keep the proxy's
  buffering off for `/mcp` — SSE responses must stream.
