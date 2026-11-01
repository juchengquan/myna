from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, status

from myna.config import Settings, get_settings


def _check_bearer(
    key: str | None,
    key_setting: str,
    env: str,
    authorization: str | None,
) -> None:
    # An empty key counts as unset: `cp .env.example .env` leaves keys blank.
    if not key:
        if env != "development":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{key_setting} not configured",
            )
        return

    expected = f"Bearer {key}"
    if authorization is None or not secrets.compare_digest(
        authorization.encode(), expected.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_admin(
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None),
) -> None:
    _check_bearer(settings.admin_api_key, "admin_api_key", settings.env, authorization)


async def require_metrics(
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None),
) -> None:
    _check_bearer(settings.metrics_api_key, "metrics_api_key", settings.env, authorization)
