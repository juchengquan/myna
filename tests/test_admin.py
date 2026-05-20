from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_tools_in_dev_without_auth(client: TestClient) -> None:
    resp = client.get("/api/admin/tools")
    assert resp.status_code == 200
    body = resp.json()
    names = {t["name"] for t in body["tools"]}
    assert {"ping", "echo"}.issubset(names)
