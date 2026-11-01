from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myna.main import create_app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = create_app()
    # The MCP endpoint only accepts local hosts that include a port.
    with TestClient(app, base_url="http://localhost:8000") as c:
        yield c
