from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myna.main import create_app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = create_app()
    # The MCP endpoint rejects TestClient's default `testserver` host.
    with TestClient(app, base_url="http://localhost") as c:
        yield c
