from datetime import date, datetime, time
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskType(StrEnum):
    MY_TASK = "MY_TASK"
    DELEGATED = "DELEGATED"
    AWAITING = "AWAITING"


class TaskStatus(StrEnum):
    NEW = "NEW"
    OPEN = "NEW"  # legacy service/test compatibility
    UNKNOWN_PARTY = "UNKNOWN_PARTY"
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    DONE = "DONE"
    OVERDUE = "OVERDUE"
    POSTPONED = "POSTPONED"
    ON_HOLD = "ON_HOLD"
    CANCELLED = "CANCELLED"

    @classmethod
    def _missing_(cls, value: object) -> "TaskStatus | None":
        if isinstance(value, str):
            normalized = value.upper()
            normalized = {
                "OPEN": "NEW",
                "IN_PROGRESS": "IN_PROGRESS",
                "DONE": "DONE",
                "CANCELLED": "CANCELLED",
            }.get(normalized, normalized)
            for member in cls:
                if member.value == normalized:
                    return member
        return None


class TaskPriority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"

    @classmethod
    def _missing_(cls, value: object) -> "TaskPriority | None":
        if isinstance(value, str):
            normalized = value.upper()
            legacy = {"LOW": "P4", "NORMAL": "P3", "HIGH": "P1"}
            normalized = legacy.get(normalized, normalized)
            for member in cls:
                if member.value == normalized:
                    return member
        return None


class DuePrecision(StrEnum):
    EXACT = "EXACT"
    DATE = "DATE"
    APPROXIMATE = "APPROXIMATE"
    UNKNOWN = "UNKNOWN"


class RelationType(StrEnum):
    COLLEAGUE = "COLLEAGUE"
    FAMILY = "FAMILY"
    FRIEND = "FRIEND"
    CLIENT = "CLIENT"
    CONTRACTOR = "CONTRACTOR"
    OTHER = "OTHER"


class TrustLevel(StrEnum):
    TRUSTED = "TRUSTED"
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    AUTOMATED = "AUTOMATED"


class ProcessingStatus(StrEnum):
    NEW = "NEW"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"


class CandidateStatus(StrEnum):
    PENDING = "PENDING"
    NOTIFIED = "NOTIFIED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PollRunTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL_API = "manual_api"
    TELEGRAM = "telegram"


class PollRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ReminderStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    RETRY = "RETRY"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class DigestType(StrEnum):
    MORNING = "MORNING"
    EVENING = "EVENING"
    WEEKLY = "WEEKLY"


class IntegrationProvider(StrEnum):
    GMAIL = "GMAIL"
    GOOGLE_CALENDAR = "GOOGLE_CALENDAR"


class IntegrationStatus(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    telegram_user_id: Mapped[int | None] = mapped_column(unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    language: Mapped[str] = mapped_column(String(16), default="ru")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    relation_type: Mapped[RelationType] = mapped_column(String(32), default=RelationType.OTHER)
    trust_level: Mapped[TrustLevel] = mapped_column(String(32), default=TrustLevel.KNOWN)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_tasks_user_idempotency_key"),
        Index("idx_tasks_user_status", "user_id", "status"),
        Index("idx_tasks_user_due", "user_id", "due_at"),
        Index("idx_tasks_assignee", "assignee_contact_id"),
        Index("idx_tasks_next_check", "next_check_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_type: Mapped[TaskType] = mapped_column(String(32), default=TaskType.MY_TASK)
    status: Mapped[TaskStatus] = mapped_column(String(32), default=TaskStatus.NEW)
    priority: Mapped[TaskPriority] = mapped_column(String(8), default=TaskPriority.P3)
    assignee_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id"), nullable=True
    )
    created_by_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id"), nullable=True
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_precision: Mapped[DuePrecision] = mapped_column(String(32), default=DuePrecision.UNKNOWN)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_sync_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    parent_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    recurrence_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    extra: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(100), default="api")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceMessage(Base):
    __tablename__ = "source_messages"
    __table_args__ = (
        UniqueConstraint("user_id", "source_type", "external_id", name="uq_source_external"),
        Index("ix_source_messages_user_source_received", "user_id", "source_type", "received_at"),
        Index(
            "ix_source_messages_user_status_created", "user_id", "processing_status", "created_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(255))
    sender_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        String(32), default=ProcessingStatus.NEW
    )
    classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extra: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskSource(Base):
    __tablename__ = "task_sources"
    __table_args__ = (
        UniqueConstraint("task_id", "source_message_id", "relation", name="uq_task_source"),
        Index("ix_task_sources_source_message", "source_message_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    source_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_messages.id", ondelete="CASCADE")
    )
    relation: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        Index("ix_task_events_task_created", "task_id", "created_at"),
        Index("ix_task_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(64))
    actor_type: Mapped[str] = mapped_column(String(32), default="USER")
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_value: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_messages.id"), nullable=True
    )
    extra: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Kept as a compatibility name for the original MVP tests and callers.
AuditEvent = TaskEvent


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reminder_type: Mapped[str] = mapped_column(String(32))
    recipient_type: Mapped[str] = mapped_column(String(32), default="OWNER")
    status: Mapped[ReminderStatus] = mapped_column(String(32), default=ReminderStatus.PENDING)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    extra: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    morning_digest_time: Mapped[time] = mapped_column(Time, default=time(7, 0))
    evening_digest_time: Mapped[time] = mapped_column(Time, default=time(19, 0))
    weekly_review_day: Mapped[int] = mapped_column(Integer, default=1)
    quiet_hours_start: Mapped[time] = mapped_column(Time, default=time(22, 0))
    quiet_hours_end: Mapped[time] = mapped_column(Time, default=time(7, 0))
    auto_create_owner_tasks: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_create_external_tasks: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_calendar: Mapped[bool] = mapped_column(Boolean, default=False)
    gmail_poll_minutes: Mapped[int] = mapped_column(Integer, default=15)
    default_priority: Mapped[TaskPriority] = mapped_column(String(8), default=TaskPriority.P3)
    extra: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class IntegrationAccount(Base):
    __tablename__ = "integration_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_integration_account_user_provider"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[IntegrationProvider] = mapped_column(String(32))
    external_account_id: Mapped[str | None] = mapped_column(String(320), nullable=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[IntegrationStatus] = mapped_column(
        String(32), default=IntegrationStatus.CONNECTED
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class IntegrationPollRun(Base):
    __tablename__ = "integration_poll_runs"
    __table_args__ = (
        Index("ix_poll_runs_account_started", "account_id", "started_at"),
        Index("ix_poll_runs_user_started", "user_id", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("integration_accounts.id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[PollRunTrigger] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[PollRunStatus] = mapped_column(String(32), nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stored_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ignored_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notified_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class TaskCandidate(Base):
    __tablename__ = "task_candidates"
    __table_args__ = (
        UniqueConstraint("source_message_id", name="uq_task_candidates_source_message"),
        Index("ix_task_candidates_user_status_detected", "user_id", "status", "detected_at"),
        Index("ix_task_candidates_user_detected", "user_id", "detected_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_messages.id", ondelete="CASCADE"), nullable=False
    )
    classification: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[CandidateStatus] = mapped_column(
        String(32), default=CandidateStatus.PENDING, nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notification_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )


class DigestDelivery(Base):
    __tablename__ = "digest_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "digest_type",
            "digest_date",
            name="uq_digest_delivery_user_type_date",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    digest_type: Mapped[DigestType] = mapped_column(String(32), default=DigestType.MORNING)
    digest_date: Mapped[date] = mapped_column(Date)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
