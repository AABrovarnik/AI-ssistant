import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.db.session import engine, session_factory
from app.tasks.models import AuditEvent, Task, TaskStatus
from app.tasks.schemas import TaskCreate, TaskUpdate
from app.tasks.service import TaskService
from sqlalchemy import delete, select

pytestmark = pytest.mark.postgres


def _postgres_enabled() -> bool:
    return os.getenv("RUN_POSTGRES_TESTS") == "1" and engine.url.drivername.startswith("postgresql")


@pytest.mark.asyncio
async def test_concurrent_create_with_same_key_is_idempotent() -> None:
    if not _postgres_enabled():
        pytest.skip("set RUN_POSTGRES_TESTS=1 with a PostgreSQL DATABASE_URL")
    key = f"pg-concurrent-{uuid4()}"
    data = TaskCreate(title="Concurrent task", idempotency_key=key)

    async def create() -> Task:
        async with session_factory() as session:
            return await TaskService(session).create(data)

    first, second = await asyncio.gather(create(), create())
    assert first.id == second.id

    async with session_factory() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.idempotency_key == key))
        await session.execute(delete(Task).where(Task.idempotency_key == key))
        await session.commit()


@pytest.mark.asyncio
async def test_failed_transition_does_not_change_persisted_task() -> None:
    if not _postgres_enabled():
        pytest.skip("set RUN_POSTGRES_TESTS=1 with a PostgreSQL DATABASE_URL")
    key = f"pg-rollback-{uuid4()}"
    async with session_factory() as session:
        task = await TaskService(session).create(
            TaskCreate(title="Rollback task", idempotency_key=key)
        )
        await TaskService(session).update(
            task.id,
            TaskUpdate(status=TaskStatus.DONE),
            f"{key}-complete",
        )
        with pytest.raises(ValueError):
            await TaskService(session).update(
                task.id,
                TaskUpdate(status=TaskStatus.OPEN),
                f"{key}-invalid",
            )
        await session.rollback()

    async with session_factory() as session:
        persisted = await session.scalar(select(Task).where(Task.idempotency_key == key))
        assert persisted is not None
        assert persisted.status == TaskStatus.DONE
        await session.execute(delete(AuditEvent).where(AuditEvent.task_id == persisted.id))
        await session.execute(delete(Task).where(Task.id == persisted.id))
        await session.commit()


@pytest.mark.asyncio
async def test_postponed_task_can_be_completed() -> None:
    if not _postgres_enabled():
        pytest.skip("set RUN_POSTGRES_TESTS=1 with a PostgreSQL DATABASE_URL")
    key = f"pg-postponed-complete-{uuid4()}"
    async with session_factory() as session:
        service = TaskService(session)
        task = await service.create(TaskCreate(title="Postponed task", idempotency_key=key))
        task = await service.postpone(
            task.id,
            datetime.now(UTC) + timedelta(days=1),
            task.version,
            f"{key}-postpone",
        )
        completed = await service.complete(task.id, f"{key}-complete", task.version)

        assert completed.status == TaskStatus.DONE

        await session.execute(delete(AuditEvent).where(AuditEvent.task_id == task.id))
        await session.execute(delete(Task).where(Task.id == task.id))
        await session.commit()
