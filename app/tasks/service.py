import builtins
import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.models import (
    Contact,
    SourceMessage,
    Task,
    TaskEvent,
    TaskSource,
    TaskStatus,
    User,
    UserSettings,
)
from app.tasks.schemas import ContactCreate, SourceMessageCreate, TaskCreate, TaskUpdate

SYSTEM_USER_ID = uuid5(NAMESPACE_URL, "ai-secretary:internal-owner")


class TaskNotFoundError(LookupError):
    pass


class InvalidTaskTransitionError(ValueError):
    pass


class VersionConflictError(ValueError):
    pass


class DuplicateSourceError(ValueError):
    pass


ACTIVE_STATUSES = {
    TaskStatus.NEW,
    TaskStatus.PLANNED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.WAITING,
    TaskStatus.OVERDUE,
    TaskStatus.POSTPONED,
    TaskStatus.ON_HOLD,
}

ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.NEW: {
        TaskStatus.PLANNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.WAITING,
        TaskStatus.DONE,
    },
    TaskStatus.PLANNED: {TaskStatus.IN_PROGRESS, TaskStatus.WAITING},
    TaskStatus.IN_PROGRESS: {
        TaskStatus.WAITING,
        TaskStatus.DONE,
        TaskStatus.POSTPONED,
        TaskStatus.ON_HOLD,
    },
    TaskStatus.WAITING: {TaskStatus.IN_PROGRESS, TaskStatus.DONE, TaskStatus.POSTPONED},
    TaskStatus.POSTPONED: {TaskStatus.PLANNED, TaskStatus.IN_PROGRESS},
    TaskStatus.ON_HOLD: {TaskStatus.PLANNED, TaskStatus.IN_PROGRESS},
    TaskStatus.OVERDUE: {TaskStatus.DONE, TaskStatus.POSTPONED, TaskStatus.IN_PROGRESS},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ensure_user(self, user_id: UUID | None = None) -> User:
        actual_id = user_id or SYSTEM_USER_ID
        user = await self.session.get(User, actual_id)
        if user is None:
            user = User(id=actual_id, name="API Owner")
            self.session.add(user)
            self.session.add(UserSettings(user_id=actual_id))
            try:
                await self.session.flush()
            except IntegrityError:
                # Two first requests for the same owner may race before the
                # task idempotency check. Reuse the committed owner.
                await self.session.rollback()
                user = await self.session.get(User, actual_id)
                if user is None:
                    raise
        return user

    async def create(self, data: TaskCreate) -> Task:
        user = await self.ensure_user(data.user_id)
        key = data.idempotency_key or str(uuid4())
        existing = await self.session.scalar(
            select(Task).where(
                Task.idempotency_key == key,
                Task.deleted_at.is_(None),
            )
        )
        if existing:
            return existing

        values = data.model_dump(exclude={"user_id", "idempotency_key", "source_message_id"})
        task = Task(**values, user_id=user.id, idempotency_key=key)
        self.session.add(task)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.session.scalar(
                select(Task).where(Task.idempotency_key == key, Task.deleted_at.is_(None))
            )
            if existing is None:
                raise
            return cast(Task, existing)

        source_message_id = data.source_message_id
        if source_message_id is not None:
            source = await self.session.scalar(
                select(SourceMessage).where(
                    SourceMessage.id == source_message_id, SourceMessage.user_id == user.id
                )
            )
            if source is None:
                raise ValueError("source message not found")
            self.session.add(
                TaskSource(
                    task_id=task.id,
                    source_message_id=source.id,
                    relation="CREATED_FROM",
                )
            )
        self._event(
            task,
            user.id,
            "TASK_CREATED",
            new_value={"title": task.title, "task_type": task.task_type.value},
            idempotency_key=key,
        )
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def get(self, task_id: UUID, user_id: UUID | None = None) -> Task:
        statement = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
        if user_id is not None:
            statement = statement.where(Task.user_id == user_id)
        task = await self.session.scalar(statement)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    async def list(
        self,
        status: TaskStatus | None = None,
        task_type: str | None = None,
        priority: str | None = None,
        assignee_contact_id: UUID | None = None,
        due_from: datetime | None = None,
        due_to: datetime | None = None,
        query: str | None = None,
        limit: int = 100,
        user_id: UUID | None = None,
    ) -> list[Task]:
        statement = select(Task).where(Task.deleted_at.is_(None)).order_by(Task.created_at.desc())
        if user_id is not None:
            statement = statement.where(Task.user_id == user_id)
        if status is not None:
            statement = statement.where(Task.status == status.value.upper())
        if task_type is not None:
            statement = statement.where(Task.task_type == task_type)
        if priority is not None:
            statement = statement.where(Task.priority == priority)
        if assignee_contact_id is not None:
            statement = statement.where(Task.assignee_contact_id == assignee_contact_id)
        if due_from is not None:
            statement = statement.where(Task.due_at >= due_from)
        if due_to is not None:
            statement = statement.where(Task.due_at < due_to)
        if query:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(Task.title.ilike(pattern), Task.description.ilike(pattern))
            )
        return list((await self.session.scalars(statement.limit(limit))).all())

    async def list_overdue(self, user_id: UUID | None = None) -> builtins.list[Task]:
        now = datetime.now(UTC)
        statement = select(Task).where(
            Task.deleted_at.is_(None),
            Task.due_at < now,
            Task.status.in_([status.value.upper() for status in ACTIVE_STATUSES]),
        )
        if user_id is not None:
            statement = statement.where(Task.user_id == user_id)
        return list((await self.session.scalars(statement.order_by(Task.due_at))).all())

    async def update(
        self,
        task_id: UUID,
        data: TaskUpdate,
        operation_key: str | None = None,
        user_id: UUID | None = None,
    ) -> Task:
        task = await self.get(task_id, user_id)
        if operation_key:
            processed = await self.session.scalar(
                select(TaskEvent).where(TaskEvent.idempotency_key == operation_key)
            )
            if processed:
                return task
        self._check_version(task, data.version)
        values = data.model_dump(exclude_unset=True, exclude={"version", "comment"})
        new_status = values.get("status")
        if new_status is not None and new_status != task.status:
            self._validate_transition(TaskStatus(task.status), TaskStatus(new_status))
            task.completed_at = datetime.now(UTC) if new_status == TaskStatus.DONE else None
        old_value = {key: getattr(task, key) for key in values}
        for key, value in values.items():
            setattr(task, key, value.value if hasattr(value, "value") else value)
        task.version += 1
        self._event(
            task,
            task.user_id,
            "STATUS_CHANGED" if new_status is not None else "TASK_UPDATED",
            old_value=self._json_safe(old_value),
            new_value=self._json_safe(values),
            idempotency_key=operation_key,
        )
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def complete(
        self,
        task_id: UUID,
        operation_key: str | None = None,
        version: int | None = None,
        user_id: UUID | None = None,
    ) -> Task:
        return await self.update(
            task_id,
            TaskUpdate(status=TaskStatus.DONE, version=version),
            operation_key,
            user_id,
        )

    async def postpone(
        self,
        task_id: UUID,
        new_due_at: datetime,
        version: int | None,
        operation_key: str | None = None,
        user_id: UUID | None = None,
    ) -> Task:
        return await self.update(
            task_id,
            TaskUpdate(
                due_at=new_due_at,
                status=TaskStatus.POSTPONED,
                version=version,
            ),
            operation_key,
            user_id,
        )

    async def cancel(
        self,
        task_id: UUID,
        version: int | None,
        operation_key: str | None = None,
        user_id: UUID | None = None,
    ) -> Task:
        task = await self.get(task_id, user_id)
        if operation_key:
            processed = await self.session.scalar(
                select(TaskEvent).where(TaskEvent.idempotency_key == operation_key)
            )
            if processed:
                return task
        self._check_version(task, version)
        task.status = TaskStatus.CANCELLED
        task.deleted_at = None
        task.version += 1
        self._event(task, task.user_id, "TASK_CANCELLED", idempotency_key=operation_key)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def create_contact(self, data: ContactCreate, user_id: UUID | None = None) -> Contact:
        user = await self.ensure_user(user_id)
        contact = Contact(**data.model_dump(), user_id=user.id)
        self.session.add(contact)
        await self.session.commit()
        await self.session.refresh(contact)
        return contact

    async def create_source_message(
        self, data: SourceMessageCreate, user_id: UUID | None = None
    ) -> SourceMessage:
        user = await self.ensure_user(user_id)
        existing = await self.session.scalar(
            select(SourceMessage).where(
                SourceMessage.user_id == user.id,
                SourceMessage.source_type == data.source_type,
                SourceMessage.external_id == data.external_id,
            )
        )
        if existing:
            raise DuplicateSourceError(existing.id)
        source = SourceMessage(**data.model_dump(), user_id=user.id)
        self.session.add(source)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DuplicateSourceError(data.external_id) from exc
        await self.session.refresh(source)
        return source

    @staticmethod
    def _check_version(task: Task, version: int | None) -> None:
        if version is not None and task.version != version:
            raise VersionConflictError(
                f"version conflict: expected {version}, current {task.version}"
            )

    @staticmethod
    def _validate_transition(current: TaskStatus, new: TaskStatus) -> None:
        if new == TaskStatus.CANCELLED and current not in {TaskStatus.DONE, TaskStatus.CANCELLED}:
            return
        if new == current:
            return
        if new not in ALLOWED_TRANSITIONS.get(current, set()):
            raise InvalidTaskTransitionError(f"cannot transition {current} to {new}")

    def _event(
        self,
        task: Task,
        user_id: UUID,
        event_type: str,
        old_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        self.session.add(
            TaskEvent(
                task_id=task.id,
                user_id=user_id,
                event_type=event_type,
                actor_type="USER",
                old_value=old_value,
                new_value=new_value,
                idempotency_key=idempotency_key,
            )
        )

    @staticmethod
    def _json_safe(values: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(json.dumps(values, default=str)))
