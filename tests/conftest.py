import os
from collections.abc import AsyncIterator

import pytest_asyncio

# Local tests must not depend on the Docker Compose hostname `db` or write to
# the working PostgreSQL database.  PostgreSQL tests are opt-in and must use a
# separately provisioned test database.
if os.getenv("RUN_POSTGRES_TESTS") != "1":
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(autouse=True)
async def create_schema() -> AsyncIterator[None]:
    from app.db.base import Base
    from app.db.session import engine
    from app.tasks import models  # noqa: F401

    if not engine.url.drivername.startswith("sqlite"):
        try:
            yield
        finally:
            await engine.dispose()
        return
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
