from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_tools_in_dev_without_auth(client: TestClient) -> None:
    resp = client.get("/api/admin/tools")
    assert resp.status_code == 200
    body = resp.json()
    names = {t["name"] for t in body["tools"]}
    assert "probe_ping" in names


def test_list_resources(client: TestClient) -> None:
    resp = client.get("/api/admin/resources")
    assert resp.status_code == 200
    body = resp.json()
    by_name = {r["name"]: r for r in body["resources"]}
    assert "probe-info" in by_name
    assert by_name["probe-info"]["is_template"] is False


def test_list_prompts(client: TestClient) -> None:
    resp = client.get("/api/admin/prompts")
    assert resp.status_code == 200
    body = resp.json()
    by_name = {p["name"]: p for p in body["prompts"]}
    assert "probe_prompt" in by_name
    arg_names = {a["name"] for a in by_name["probe_prompt"]["arguments"]}
    assert "text" in arg_names
