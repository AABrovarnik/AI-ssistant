"""Task Core HTTP API."""
# ruff: noqa: B008

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_internal_api_token
from app.db.session import get_db_session
from app.tasks.models import Task, TaskStatus, TaskType
from app.tasks.schemas import (
    PostponeRequest,
    StatusRequest,
    TaskAction,
    TaskCreate,
    TaskRead,
    TaskUpdate,
)
from app.tasks.service import (
    InvalidTaskTransitionError,
    TaskNotFoundError,
    TaskService,
    VersionConflictError,
)

router = APIRouter(
    prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_internal_api_token)]
)


def service(session: AsyncSession = Depends(get_db_session)) -> TaskService:
    return TaskService(session)


def operation_key(header_value: str | None) -> str:
    return header_value or str(uuid4())


def mutation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TaskNotFoundError):
        return HTTPException(status_code=404, detail="task not found")
    if isinstance(exc, VersionConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, InvalidTaskTransitionError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(data: TaskCreate, tasks: TaskService = Depends(service)) -> object:
    try:
        return await tasks.create(data)
    except ValueError as exc:
        raise mutation_error(exc) from exc


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    task_status: TaskStatus | None = Query(default=None, alias="status"),
    task_type: TaskType | None = None,
    priority: str | None = None,
    assignee_contact_id: UUID | None = None,
    due_from: datetime | None = None,
    due_to: datetime | None = None,
    query: str | None = Query(default=None, alias="q"),
    limit: int = Query(default=100, ge=1, le=100),
    tasks: TaskService = Depends(service),
) -> list[Task]:
    return await tasks.list(
        task_status,
        task_type,
        priority,
        assignee_contact_id,
        due_from,
        due_to,
        query,
        limit,
    )


@router.get("/views/today", response_model=list[TaskRead])
async def today_tasks(tasks: TaskService = Depends(service)) -> list[Task]:
    now = datetime.now(UTC)
    return await tasks.list(
        due_from=now.replace(hour=0, minute=0, second=0, microsecond=0),
        due_to=now + timedelta(days=1),
    )


@router.get("/views/week", response_model=list[TaskRead])
async def week_tasks(tasks: TaskService = Depends(service)) -> list[Task]:
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return await tasks.list(due_from=start, due_to=start + timedelta(days=7))


@router.get("/views/overdue", response_model=list[TaskRead])
async def overdue_tasks(tasks: TaskService = Depends(service)) -> list[Task]:
    return await tasks.list_overdue()


@router.get("/views/delegated", response_model=list[TaskRead])
async def delegated_tasks(tasks: TaskService = Depends(service)) -> list[Task]:
    return await tasks.list(task_type=TaskType.DELEGATED)


@router.get("/views/waiting", response_model=list[TaskRead])
async def waiting_tasks(tasks: TaskService = Depends(service)) -> list[Task]:
    return await tasks.list(task_type=TaskType.AWAITING)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: UUID, tasks: TaskService = Depends(service)) -> object:
    try:
        return await tasks.get(task_id)
    except TaskNotFoundError as exc:
        raise mutation_error(exc) from exc


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: UUID,
    data: TaskUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    tasks: TaskService = Depends(service),
) -> object:
    try:
        return await tasks.update(task_id, data, operation_key(idempotency_key))
    except (TaskNotFoundError, VersionConflictError, InvalidTaskTransitionError) as exc:
        raise mutation_error(exc) from exc


@router.post("/{task_id}/complete", response_model=TaskRead)
async def complete_task(
    task_id: UUID,
    data: TaskAction | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    tasks: TaskService = Depends(service),
) -> object:
    action = data or TaskAction()
    try:
        return await tasks.complete(
            task_id, operation_key(idempotency_key), action.version
        )
    except (TaskNotFoundError, VersionConflictError, InvalidTaskTransitionError) as exc:
        raise mutation_error(exc) from exc


@router.post("/{task_id}/postpone", response_model=TaskRead)
async def postpone_task(
    task_id: UUID,
    data: PostponeRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    tasks: TaskService = Depends(service),
) -> object:
    try:
        return await tasks.postpone(
            task_id,
            data.new_due_at,
            data.version,
            operation_key(idempotency_key),
        )
    except (TaskNotFoundError, VersionConflictError, InvalidTaskTransitionError) as exc:
        raise mutation_error(exc) from exc


@router.post("/{task_id}/status", response_model=TaskRead)
async def set_task_status(
    task_id: UUID,
    data: StatusRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    tasks: TaskService = Depends(service),
) -> object:
    try:
        return await tasks.update(
            task_id,
            TaskUpdate(status=data.status, version=data.version, comment=data.comment),
            operation_key(idempotency_key),
        )
    except (TaskNotFoundError, VersionConflictError, InvalidTaskTransitionError) as exc:
        raise mutation_error(exc) from exc


@router.post("/{task_id}/cancel", response_model=TaskRead)
async def cancel_task(
    task_id: UUID,
    data: TaskAction | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    tasks: TaskService = Depends(service),
) -> object:
    action = data or TaskAction()
    try:
        return await tasks.cancel(
            task_id, action.version, operation_key(idempotency_key)
        )
    except (TaskNotFoundError, VersionConflictError, InvalidTaskTransitionError) as exc:
        raise mutation_error(exc) from exc
