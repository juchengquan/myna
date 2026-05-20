from __future__ import annotations

from fastapi.testclient import TestClient


def test_mcp_endpoint_is_mounted(client: TestClient) -> None:
    # The MCP Streamable HTTP endpoint expects POST requests with the
    # MCP-specific Accept header. We don't replay the full MCP handshake
    # here — we just verify the mount is wired up by sending a POST and
    # asserting we reach the MCP app (it should reject with a 4xx, not
    # 404 from the outer FastAPI router).
    resp = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code != 404
    assert resp.status_code < 500
