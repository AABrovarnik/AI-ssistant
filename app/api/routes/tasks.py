"""Task HTTP API."""
# FastAPI dependency/query declarations intentionally use call expressions.
# ruff: noqa: B008

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_internal_api_token
from app.db.session import get_db_session
from app.tasks.models import Task, TaskStatus
from app.tasks.schemas import TaskCreate, TaskRead, TaskUpdate
from app.tasks.service import InvalidTaskTransitionError, TaskNotFoundError, TaskService

router = APIRouter(
    prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_internal_api_token)]
)


def service(session: AsyncSession = Depends(get_db_session)) -> TaskService:
    return TaskService(session)


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(data: TaskCreate, tasks: TaskService = Depends(service)) -> Task:
    return await tasks.create(data)


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    task_status: TaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=100),
    tasks: TaskService = Depends(service),
) -> list[Task]:
    return await tasks.list(task_status, limit)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: UUID, tasks: TaskService = Depends(service)) -> Task:
    try:
        return await tasks.get(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: UUID,
    data: TaskUpdate,
    idempotency_key: str = Header(..., min_length=1, alias="Idempotency-Key"),
    tasks: TaskService = Depends(service),
) -> Task:
    try:
        return await tasks.update(task_id, data, idempotency_key)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except InvalidTaskTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/complete", response_model=TaskRead)
async def complete_task(
    task_id: UUID,
    idempotency_key: str = Header(..., min_length=1, alias="Idempotency-Key"),
    tasks: TaskService = Depends(service),
) -> Task:
    try:
        return await tasks.complete(task_id, idempotency_key)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except InvalidTaskTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
