from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from myna.keys.store import metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(context.config.attributes["url"], poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


asyncio.run(_main())
