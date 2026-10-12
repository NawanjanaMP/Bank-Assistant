"""Alembic environment: uses BANKAI_DATABASE_URL, runs async migrations."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from bankai.orchestrator.db import Base
from bankai.shared.config import get_settings

target_metadata = Base.metadata


def _run(connection) -> None:  # noqa: ANN001
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
        await conn.commit()
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(main())
