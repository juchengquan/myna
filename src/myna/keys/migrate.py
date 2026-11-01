"""Database migrations for the key store, and the `myna-migrate` command.

Migrations run as an explicit step, not at startup, so replicas starting at the
same time can't race each other.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from myna.config import get_settings
from myna.keys.store import normalize_database_url
from myna.logging_config import get_logger

_MIGRATIONS = Path(__file__).parent / "migrations"

_log = get_logger("myna.keys")


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    # Passed as an attribute, because config options treat `%` in passwords specially.
    config.attributes["url"] = normalize_database_url(url)
    return config


def upgrade(url: str) -> None:
    command.upgrade(alembic_config(url), "head")


async def check_schema(engine: AsyncEngine, url: str) -> None:
    """Stop startup if migrations are behind.

    An unreachable database only logs a warning, so a restart during an outage
    still brings the server up for clients using env keys.
    """
    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    try:
        async with engine.connect() as connection:
            current = await connection.run_sync(
                lambda sync: MigrationContext.configure(sync).get_current_revision()
            )
    except (SQLAlchemyError, OSError, TimeoutError):
        _log.warning("key_store_unreachable_at_startup")
        return
    if current != head:
        raise RuntimeError(
            f"The key store schema is at {current}, but this version needs {head}. "
            "Run myna-migrate."
        )


def main() -> None:
    url = get_settings().database_url
    if not url:
        sys.exit("MYNA_DATABASE_URL isn't set.")
    try:
        upgrade(url)
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        sys.exit(f"Migration failed: {type(exc).__name__}. Check that the database is reachable.")
    print("Key store schema is up to date.")


if __name__ == "__main__":
    main()
