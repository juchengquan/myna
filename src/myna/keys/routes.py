"""Create, list, and revoke database-backed API keys.

These routes use `MYNA_KEY_ADMIN_API_KEY` rather than the admin key, because a
key that can create MCP keys can reach every tool. The admin key stays read-only.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from myna.config import Settings, get_settings, validate_key_name
from myna.keys.store import KeyLimitReached, KeyRecord, KeyStore
from myna.logging_config import get_logger
from myna.observability import RATE_LIMIT_HITS

_audit = get_logger("myna.audit")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def require_key_admin(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = f"Bearer {settings.key_admin_api_key}"
    if authorization is not None and secrets.compare_digest(
        authorization.encode(), expected.encode()
    ):
        return

    allowed, retry_after = await request.app.state.rate_limiter.check(
        f"auth-failure:{_client_ip(request)}"
    )
    if not allowed:
        RATE_LIMIT_HITS.labels(key_kind="auth_failure").inc()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts",
            headers={"Retry-After": str(max(1, int(retry_after) + 1))},
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _store(request: Request) -> KeyStore:
    store: KeyStore = request.app.state.key_store
    return store


router = APIRouter(
    prefix="/admin/keys", tags=["keys"], dependencies=[Depends(require_key_admin)]
)


class CreateKeyRequest(BaseModel):
    name: str = Field(max_length=64, description="The caller name in metrics and audit logs.")
    expires_in_days: int | None = Field(
        default=90, ge=1, le=3650, description="Days until the key expires. null never expires."
    )

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        problem = validate_key_name(value)
        if problem:
            raise ValueError(problem)
        return value


class KeyInfo(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class CreatedKey(KeyInfo):
    key: str = Field(description="The key itself. It's only shown once.")


class KeysResponse(BaseModel):
    keys: list[KeyInfo]


def _info(record: KeyRecord, now: datetime) -> dict[str, object]:
    return {
        "id": record.id,
        "name": record.name,
        "status": record.status(now),
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "last_used_at": record.last_used_at,
        "revoked_at": record.revoked_at,
    }


@router.post("", response_model=CreatedKey, status_code=status.HTTP_201_CREATED)
async def create_key(
    body: CreateKeyRequest,
    request: Request,
    store: Annotated[KeyStore, Depends(_store)],
) -> CreatedKey:
    try:
        record, key = await store.create(body.name, expires_in_days=body.expires_in_days)
    except KeyLimitReached:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MYNA_MAX_ACTIVE_KEYS reached. Revoke a key or raise the limit.",
        ) from None
    _audit.info(
        "key_created",
        key_id=record.id,
        name=record.name,
        expires_at=record.expires_at.isoformat() if record.expires_at else None,
        client_ip=_client_ip(request),
    )
    return CreatedKey.model_validate({**_info(record, store.now()), "key": key})


@router.get("", response_model=KeysResponse)
async def list_keys(store: Annotated[KeyStore, Depends(_store)]) -> KeysResponse:
    now = store.now()
    records = await store.list_keys()
    return KeysResponse(keys=[KeyInfo.model_validate(_info(r, now)) for r in records])


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: str,
    request: Request,
    store: Annotated[KeyStore, Depends(_store)],
) -> Response:
    record = await store.revoke(key_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown key")
    _audit.info("key_revoked", key_id=record.id, name=record.name, client_ip=_client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
