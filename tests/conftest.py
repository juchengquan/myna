from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myna import mcp_server
from myna.main import create_app
from tests import probes


@pytest.fixture(autouse=True)
def _register_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mcp_server, "REGISTRATIONS", [*mcp_server.REGISTRATIONS, probes.register]
    )


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = create_app()
    # The MCP endpoint only accepts local hosts that include a port.
    with TestClient(app, base_url="http://localhost:8000") as c:
        yield c
