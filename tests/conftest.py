import os
from collections.abc import AsyncIterator

import pytest_asyncio

# Local tests must not depend on the Docker Compose hostname `db`.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest_asyncio.fixture(autouse=True)
async def create_schema() -> AsyncIterator[None]:
    from app.db.base import Base
    from app.db.session import engine
    from app.tasks import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
