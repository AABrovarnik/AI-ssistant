import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.models import AuditEvent, Task, TaskStatus
from app.tasks.schemas import TaskCreate, TaskUpdate


class TaskNotFoundError(LookupError):
    pass


class InvalidTaskTransitionError(ValueError):
    pass


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: TaskCreate) -> Task:
        existing = await self.session.scalar(
            select(Task).where(Task.idempotency_key == data.idempotency_key)
        )
        if existing:
            return existing

        task = Task(**data.model_dump())
        self.session.add(task)
        await self.session.flush()
        self._audit(task, "task.created", data.idempotency_key, data.model_dump())
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def get(self, task_id: UUID) -> Task:
        task = await self.session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    async def list(self, status: TaskStatus | None = None, limit: int = 100) -> list[Task]:
        statement = select(Task).order_by(Task.created_at.desc()).limit(limit)
        if status is not None:
            statement = statement.where(Task.status == status)
        return list((await self.session.scalars(statement)).all())

    async def update(self, task_id: UUID, data: TaskUpdate, operation_key: str) -> Task:
        task = await self.get(task_id)
        processed = await self.session.scalar(
            select(AuditEvent).where(AuditEvent.idempotency_key == operation_key)
        )
        if processed:
            return task
        values = data.model_dump(exclude_unset=True)
        new_status = values.get("status")
        if new_status is not None and new_status != task.status:
            self._validate_transition(task.status, new_status)
            task.completed_at = datetime.now(UTC) if new_status == TaskStatus.DONE else None
        for key, value in values.items():
            setattr(task, key, value)
        self._audit(task, "task.updated", operation_key, values)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def complete(self, task_id: UUID, operation_key: str) -> Task:
        return await self.update(task_id, TaskUpdate(status=TaskStatus.DONE), operation_key)

    @staticmethod
    def _validate_transition(current: TaskStatus, new: TaskStatus) -> None:
        if current in {TaskStatus.DONE, TaskStatus.CANCELLED}:
            raise InvalidTaskTransitionError(f"cannot transition {current} to {new}")

    def _audit(self, task: Task, operation: str, key: str, payload: object) -> None:
        self.session.add(
            AuditEvent(
                task_id=task.id,
                operation=operation,
                idempotency_key=key,
                payload=json.dumps(payload, default=str, sort_keys=True),
            )
        )
