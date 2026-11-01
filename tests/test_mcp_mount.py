from __future__ import annotations

from fastapi.testclient import TestClient


def test_mcp_endpoint_is_mounted(client: TestClient) -> None:
    resp = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200
