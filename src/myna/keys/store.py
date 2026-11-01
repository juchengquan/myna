"""Storage for database-backed API keys.

Only a SHA-256 hash of each key is stored. Keys are 256 random bits, so a slow
password hash would add cost without adding safety, and a leaked table doesn't
expose a working key.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from myna.logging_config import get_logger
from myna.verifiers import VerifierUnavailable

KEY_PREFIX = "myna_"
LAST_USED_RESOLUTION = timedelta(minutes=1)

_ASYNC_DRIVERS = {
    "sqlite": "sqlite+aiosqlite",
    "sqlite+aiosqlite": "sqlite+aiosqlite",
    "postgres": "postgresql+asyncpg",
    "postgresql": "postgresql+asyncpg",
    "postgresql+asyncpg": "postgresql+asyncpg",
}

_log = get_logger("myna.keys")

metadata = sa.MetaData()
api_keys = sa.Table(
    "api_keys",
    metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("name", sa.String(64), nullable=False),
    sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
)


class KeyLimitReached(Exception):
    """There are already `MYNA_MAX_ACTIVE_KEYS` active keys."""


@dataclass(frozen=True)
class KeyRecord:
    id: str
    name: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    def status(self, now: datetime) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= now:
            return "expired"
        return "active"


def normalize_database_url(url: str) -> str:
    """Return `url` using an async driver. Errors never include the URL."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        raise ValueError("MYNA_DATABASE_URL isn't a valid database URL") from None
    driver = _ASYNC_DRIVERS.get(parsed.drivername)
    if driver is None:
        raise ValueError("MYNA_DATABASE_URL must be a sqlite or postgresql URL")
    return parsed.set(drivername=driver).render_as_string(hide_password=False)


def create_engine(url: str) -> AsyncEngine:
    normalized = normalize_database_url(url)
    if normalized.startswith("sqlite"):
        # SQLite connections are cheap, and this avoids tying them to one event loop.
        return create_async_engine(normalized, poolclass=NullPool, connect_args={"timeout": 5})
    # Fail fast when the database is down, so a request gets a 503 instead of hanging.
    return create_async_engine(
        normalized,
        pool_pre_ping=True,
        connect_args={"timeout": 3, "command_timeout": 5},
    )


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _utc(value: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes. Everything stored is UTC.
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _record(row: sa.Row[tuple[str, str, datetime, datetime, datetime, datetime]]) -> KeyRecord:
    created_at = _utc(row.created_at)
    assert created_at is not None
    return KeyRecord(
        id=row.id,
        name=row.name,
        created_at=created_at,
        expires_at=_utc(row.expires_at),
        last_used_at=_utc(row.last_used_at),
        revoked_at=_utc(row.revoked_at),
    )


class KeyStore:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        max_active_keys: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine = engine
        self.now = now
        self._max_active_keys = max_active_keys

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncConnection]:
        try:
            async with self.engine.begin() as connection:
                yield connection
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            raise VerifierUnavailable("key store unavailable") from exc

    def _active(self, now: datetime) -> sa.ColumnElement[bool]:
        return sa.and_(
            api_keys.c.revoked_at.is_(None),
            sa.or_(api_keys.c.expires_at.is_(None), api_keys.c.expires_at > now),
        )

    async def create(self, name: str, *, expires_in_days: int | None) -> tuple[KeyRecord, str]:
        """Create a key. The returned secret can't be retrieved again."""
        now = self.now()
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        record = KeyRecord(
            id=uuid.uuid4().hex,
            name=name,
            created_at=now,
            expires_at=None if expires_in_days is None else now + timedelta(days=expires_in_days),
            last_used_at=None,
            revoked_at=None,
        )
        async with self._transaction() as connection:
            count = await connection.scalar(
                sa.select(sa.func.count()).select_from(api_keys).where(self._active(now))
            )
            if (count or 0) >= self._max_active_keys:
                raise KeyLimitReached
            await connection.execute(
                api_keys.insert().values(
                    id=record.id,
                    name=record.name,
                    key_hash=hash_key(key),
                    created_at=record.created_at,
                    expires_at=record.expires_at,
                )
            )
        return record, key

    async def list_keys(self) -> list[KeyRecord]:
        columns = [c for c in api_keys.c if c.name != "key_hash"]
        query = sa.select(*columns).order_by(api_keys.c.created_at, api_keys.c.id)
        async with self._transaction() as connection:
            rows = (await connection.execute(query)).all()
        return [_record(row) for row in rows]

    async def revoke(self, key_id: str) -> KeyRecord | None:
        """Revoke a key. Revoking one twice keeps the first time. None if unknown."""
        now = self.now()
        columns = [c for c in api_keys.c if c.name != "key_hash"]
        async with self._transaction() as connection:
            await connection.execute(
                api_keys.update()
                .where(api_keys.c.id == key_id, api_keys.c.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            row = (
                await connection.execute(sa.select(*columns).where(api_keys.c.id == key_id))
            ).first()
        return _record(row) if row else None

    async def find_active(self, key: str) -> KeyRecord | None:
        """Return the active key matching `key`, recording that it was used."""
        now = self.now()
        columns = [c for c in api_keys.c if c.name != "key_hash"]
        query = sa.select(*columns).where(api_keys.c.key_hash == hash_key(key), self._active(now))
        async with self._transaction() as connection:
            row = (await connection.execute(query)).first()
        if row is None:
            return None
        record = _record(row)
        if record.last_used_at is None or now - record.last_used_at >= LAST_USED_RESOLUTION:
            await self._touch(record.id, now)
        return record

    async def _touch(self, key_id: str, now: datetime) -> None:
        # A failed write to a usage timestamp shouldn't refuse a valid key.
        try:
            async with self._transaction() as connection:
                await connection.execute(
                    api_keys.update().where(api_keys.c.id == key_id).values(last_used_at=now)
                )
        except VerifierUnavailable:
            _log.warning("key_last_used_write_failed", key_id=key_id)
