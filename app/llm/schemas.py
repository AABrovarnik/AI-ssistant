"""Validated contracts exchanged with the LLM parser."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.tasks.models import DuePrecision, TaskPriority, TaskStatus, TaskType


class MessageClassification(StrEnum):
    TASK = "TASK"
    DELEGATION = "DELEGATION"
    AWAITING = "AWAITING"
    CALENDAR_EVENT = "CALENDAR_EVENT"
    REMINDER = "REMINDER"
    STATUS_UPDATE = "STATUS_UPDATE"
    TASK_COMPLETE = "TASK_COMPLETE"
    INFORMATION = "INFORMATION"
    SPAM = "SPAM"
    UNCLEAR = "UNCLEAR"


class ClassificationResult(BaseModel):
    classification: MessageClassification
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="", max_length=1000)


class TaskCandidate(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    task_type: TaskType
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    assignee_name: str | None = Field(default=None, max_length=255)
    due_at: datetime | None = None
    due_date: date | None = None
    due_precision: DuePrecision = DuePrecision.UNKNOWN
    priority: TaskPriority = TaskPriority.P3
    requires_confirmation: bool = True
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(default="", max_length=2000)


class TaskExtractionResult(BaseModel):
    task_detected: bool
    candidate: TaskCandidate | None = None


class StatusDecision(StrEnum):
    DONE = "DONE"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"
    NO_CHANGE = "NO_CHANGE"


class StatusAnalysisResult(BaseModel):
    status: StatusDecision = StatusDecision.NO_CHANGE
    new_due_at: datetime | None = None
    new_due_date: date | None = None
    due_precision: DuePrecision = DuePrecision.UNKNOWN
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(default="", max_length=2000)


class SearchDateFilter(StrEnum):
    TODAY = "TODAY"
    TOMORROW = "TOMORROW"
    THIS_WEEK = "THIS_WEEK"
    NEXT_WEEK = "NEXT_WEEK"
    THIS_MONTH = "THIS_MONTH"
    NONE = "NONE"


class SearchFilters(BaseModel):
    task_type: list[TaskType] = Field(default_factory=list)
    status: list[TaskStatus] = Field(default_factory=list)
    exclude_status: list[TaskStatus] = Field(default_factory=list)
    priority: list[TaskPriority] = Field(default_factory=list)
    assignee_name: str | None = Field(default=None, max_length=255)
    date_filter: SearchDateFilter = SearchDateFilter.NONE
    overdue_days_min: int | None = Field(default=None, ge=0)
    text_query: str | None = Field(default=None, max_length=500)
    sort: str = Field(default="DUE_ASC", pattern="^(DUE_ASC|DUE_DESC|CREATED_DESC)$")
    limit: int = Field(default=50, ge=1, le=100)


class SearchParseResult(BaseModel):
    filters: SearchFilters
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(default="", max_length=2000)


class ParsedMessage(BaseModel):
    classification: ClassificationResult
    extraction: TaskExtractionResult | None = None
