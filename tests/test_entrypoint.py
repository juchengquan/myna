from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from myna.__main__ import main
from myna.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("env", "reload"),
    [("development", True), ("staging", False), ("production", False)],
)
def test_main_runs_uvicorn_from_settings(
    monkeypatch: pytest.MonkeyPatch, env: str, reload: bool
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "uvicorn.run", lambda target, **kwargs: calls.append((target, kwargs))
    )
    monkeypatch.setenv("MYNA_ENV", env)
    monkeypatch.setenv("MYNA_HOST", "127.0.0.1")
    monkeypatch.setenv("MYNA_PORT", "9001")
    monkeypatch.setenv("MYNA_LOG_LEVEL", "WARNING")

    main()

    assert calls == [
        (
            "myna.main:app",
            {"host": "127.0.0.1", "port": 9001, "log_level": "warning", "reload": reload},
        )
    ]
