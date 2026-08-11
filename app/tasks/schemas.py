from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.tasks.models import DuePrecision, TaskPriority, TaskStatus, TaskType


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    task_type: TaskType = TaskType.MY_TASK
    priority: TaskPriority = TaskPriority.P3
    assignee_contact_id: UUID | None = None
    created_by_contact_id: UUID | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    due_date: date | None = None
    due_precision: DuePrecision = DuePrecision.UNKNOWN
    next_check_at: datetime | None = None
    source_type: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    source_message_id: UUID | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    user_id: UUID | None = None
    source: str = Field(default="api", min_length=1, max_length=100)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    extra: dict[str, object] = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    version: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    task_type: TaskType | None = None
    priority: TaskPriority | None = None
    assignee_contact_id: UUID | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    due_date: date | None = None
    due_precision: DuePrecision | None = None
    next_check_at: datetime | None = None
    status: TaskStatus | None = None
    comment: str | None = None


class TaskAction(BaseModel):
    version: int | None = Field(default=None, ge=1)
    comment: str | None = None


class PostponeRequest(TaskAction):
    new_due_at: datetime


class StatusRequest(TaskAction):
    status: TaskStatus


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    title: str
    description: str | None
    task_type: TaskType
    status: TaskStatus
    priority: TaskPriority
    assignee_contact_id: UUID | None
    start_at: datetime | None
    due_at: datetime | None
    due_date: date | None
    due_precision: DuePrecision
    next_check_at: datetime | None
    completed_at: datetime | None
    version: int
    source: str
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @field_serializer("status")
    def serialize_status(self, value: TaskStatus) -> str:
        # Keep the pre-Phase-1 wire format for existing API clients.
        return value.value.lower()


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    telegram_username: str | None = None
    telegram_chat_id: int | None = None
    email: str | None = None
    relation_type: str = "OTHER"
    trust_level: str = "KNOWN"
    notes: str | None = None


class ContactRead(ContactCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


class SourceMessageCreate(BaseModel):
    source_type: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=255)
    sender_external_id: str | None = None
    sender_name: str | None = None
    sender_email: str | None = None
    subject: str | None = None
    text: str | None = None
    received_at: datetime | None = None
    thread_id: str | None = None
    source_url: str | None = None


class ReminderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    user_id: UUID
    remind_at: datetime
    reminder_type: str
    status: str
