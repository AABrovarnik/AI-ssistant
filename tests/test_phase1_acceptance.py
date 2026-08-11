from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.db.session import engine, session_factory
from app.tasks.models import (
    SourceMessage,
    Task,
    TaskEvent,
    TaskSource,
    TaskStatus,
    TaskType,
    User,
    UserSettings,
)
from app.tasks.schemas import SourceMessageCreate, TaskCreate, TaskUpdate
from app.tasks.service import (
    DuplicateSourceError,
    InvalidTaskTransitionError,
    TaskService,
    VersionConflictError,
)
from sqlalchemy import delete, select

pytestmark = pytest.mark.postgres


def _postgres_enabled() -> bool:
    return engine.url.drivername.startswith("postgresql")


@pytest.mark.asyncio
async def test_phase1_transitions_locking_overdue_and_soft_cancel() -> None:
    if not _postgres_enabled():
        pytest.skip("requires PostgreSQL")

    user_id = uuid4()
    create_key = f"phase1-task-{uuid4()}"
    async with session_factory() as session:
        service = TaskService(session)
        task = await service.create(
            TaskCreate(
                user_id=user_id,
                title="Phase 1 task",
                task_type=TaskType.MY_TASK,
                due_at=datetime.now(UTC) - timedelta(hours=1),
                idempotency_key=create_key,
            )
        )
        assert task.status == TaskStatus.NEW
        assert task.version == 1

        task = await service.postpone(
            task.id,
            datetime.now(UTC) + timedelta(days=1),
            task.version,
            f"phase1-postpone-new-{uuid4()}",
        )
        assert task.status == TaskStatus.POSTPONED
        assert task.version == 2

        with pytest.raises(VersionConflictError):
            await service.update(
                task.id,
                TaskUpdate(version=99, title="stale"),
                f"phase1-stale-{uuid4()}",
            )

        task = await service.update(
            task.id,
            TaskUpdate(version=2, status=TaskStatus.IN_PROGRESS),
            f"phase1-progress-{uuid4()}",
        )
        task = await service.update(
            task.id,
            TaskUpdate(version=3, status=TaskStatus.WAITING),
            f"phase1-waiting-{uuid4()}",
        )
        assert task.version == 4

        with pytest.raises(InvalidTaskTransitionError):
            await service.update(
                task.id,
                TaskUpdate(version=4, status=TaskStatus.NEW),
                f"phase1-invalid-{uuid4()}",
            )

        task = await service.update(
            task.id,
            TaskUpdate(version=4, due_at=datetime.now(UTC) - timedelta(hours=1)),
            f"phase1-overdue-{uuid4()}",
        )
        overdue = await service.list_overdue(user_id)
        assert any(item.id == task.id for item in overdue)

        task = await service.cancel(
            task.id, task.version, f"phase1-cancel-{uuid4()}", user_id
        )
        assert task.status == TaskStatus.CANCELLED
        assert task.deleted_at is None

        events = list(
            (
                await session.scalars(
                    select(TaskEvent).where(TaskEvent.task_id == task.id)
                )
            ).all()
        )
        assert {event.event_type for event in events} >= {
            "TASK_CREATED",
            "STATUS_CHANGED",
            "TASK_CANCELLED",
        }

        await session.execute(delete(TaskEvent).where(TaskEvent.task_id == task.id))
        await session.execute(delete(Task).where(Task.id == task.id))
        await session.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


@pytest.mark.asyncio
async def test_phase1_source_duplicate_prevention_and_linking() -> None:
    if not _postgres_enabled():
        pytest.skip("requires PostgreSQL")

    user_id = uuid4()
    external_id = f"message-{uuid4()}"
    async with session_factory() as session:
        service = TaskService(session)
        source = await service.create_source_message(
            SourceMessageCreate(source_type="TELEGRAM", external_id=external_id), user_id
        )
        with pytest.raises(DuplicateSourceError):
            await service.create_source_message(
                SourceMessageCreate(source_type="TELEGRAM", external_id=external_id), user_id
            )

        task = await service.create(
            TaskCreate(
                user_id=user_id,
                title="Linked source task",
                source_message_id=source.id,
                idempotency_key=f"phase1-source-task-{uuid4()}",
            )
        )
        link = await session.scalar(
            select(TaskSource).where(
                TaskSource.task_id == task.id,
                TaskSource.source_message_id == source.id,
            )
        )
        assert link is not None

        await session.execute(delete(TaskSource).where(TaskSource.task_id == task.id))
        await session.execute(delete(TaskEvent).where(TaskEvent.task_id == task.id))
        await session.execute(delete(Task).where(Task.id == task.id))
        await session.execute(delete(SourceMessage).where(SourceMessage.id == source.id))
        await session.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


@pytest.mark.asyncio
async def test_phase4_pending_candidate_is_reused_for_edit() -> None:
    if not _postgres_enabled():
        pytest.skip("requires PostgreSQL")

    user_id = uuid4()
    async with session_factory() as session:
        service = TaskService(session)
        source = await service.create_source_message(
            SourceMessageCreate(
                source_type="TELEGRAM",
                external_id=f"edit-{uuid4()}",
                text="старый текст",
            ),
            user_id,
        )
        source = await service.save_source_candidate(
            source.id,
            "TASK",
            0.9,
            {"title": "старый текст", "awaiting_edit": True},
            user_id,
        )
        source.extra = {**source.extra, "awaiting_edit": True}
        await session.commit()

        pending = await service.get_pending_candidate(user_id)

        assert pending is not None
        assert pending.id == source.id

        await session.delete(source)
        await session.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


@pytest.mark.asyncio
async def test_phase4_pending_task_is_reused_for_edit() -> None:
    if not _postgres_enabled():
        pytest.skip("requires PostgreSQL")

    user_id = uuid4()
    async with session_factory() as session:
        service = TaskService(session)
        task = await service.create(
            TaskCreate(
                user_id=user_id,
                title="старый заголовок",
                idempotency_key=f"edit-task-{uuid4()}",
            )
        )
        task.extra = {"awaiting_edit": True}
        await session.commit()

        pending = await service.get_pending_edit_task(user_id)

        assert pending is not None
        assert pending.id == task.id

        await session.execute(delete(TaskEvent).where(TaskEvent.task_id == task.id))
        await session.delete(task)
        await session.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()
