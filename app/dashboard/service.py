"""Owner-scoped dashboard queries.

The dashboard deliberately keeps aggregation and drill-down filters in the same
module so that a visible count and the rows behind it use the same definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.models import (
    CandidateStatus,
    IntegrationAccount,
    IntegrationPollRun,
    IntegrationProvider,
    IntegrationStatus,
    PollRunStatus,
    SourceMessage,
    Task,
    TaskCandidate,
    TaskSource,
    UserSettings,
)
from app.tasks.service import SYSTEM_USER_ID


class DashboardTimezoneError(ValueError):
    """The requested IANA timezone is not available."""


@dataclass(frozen=True)
class DashboardPeriod:
    start: datetime
    end: datetime
    timezone: str


def make_period(
    from_date: date | None,
    to_date: date | None,
    timezone: str,
    now: datetime | None = None,
) -> DashboardPeriod:
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise DashboardTimezoneError(timezone) from exc
    current = now or datetime.now(UTC)
    local_now = current.astimezone(zone)
    local_start = from_date or (local_now.date() - timedelta(days=6))
    local_end = to_date or local_now.date()
    if local_end < local_start:
        raise ValueError("dashboard period end must not be before start")
    return DashboardPeriod(
        start=datetime.combine(local_start, datetime.min.time(), tzinfo=zone).astimezone(UTC),
        end=datetime.combine(
            local_end + timedelta(days=1), datetime.min.time(), tzinfo=zone
        ).astimezone(UTC),
        timezone=timezone,
    )


class DashboardQueryService:
    def __init__(self, session: AsyncSession, period: DashboardPeriod) -> None:
        self.session = session
        self.period = period

    def _message_period(self) -> Any:
        return and_(
            SourceMessage.received_at >= self.period.start,
            SourceMessage.received_at < self.period.end,
        )

    def _candidate_period(self) -> Any:
        return and_(
            TaskCandidate.detected_at >= self.period.start,
            TaskCandidate.detected_at < self.period.end,
        )

    def _task_period(self) -> Any:
        return and_(
            TaskSource.created_at >= self.period.start,
            TaskSource.created_at < self.period.end,
        )

    async def _count(self, statement: Any) -> int:
        return int((await self.session.scalar(statement)) or 0)

    def _base_message_conditions(self) -> tuple[Any, ...]:
        return (
            SourceMessage.user_id == SYSTEM_USER_ID,
            SourceMessage.source_type == "GMAIL",
            self._message_period(),
        )

    async def overview(self) -> dict[str, Any]:
        base = self._base_message_conditions()
        detected = await self._count(select(func.count(distinct(SourceMessage.id))).where(*base))
        processed = await self._count(
            select(func.count(distinct(SourceMessage.id))).where(
                *base, SourceMessage.processing_status == "PROCESSED"
            )
        )
        filtered = await self._count(
            select(func.count(distinct(SourceMessage.id))).where(
                *base, SourceMessage.processing_status == "IGNORED"
            )
        )
        failed = await self._count(
            select(func.count(distinct(SourceMessage.id))).where(
                *base, SourceMessage.processing_status == "FAILED"
            )
        )
        candidate_count = await self._count(
            select(func.count(distinct(TaskCandidate.id))).where(
                TaskCandidate.user_id == SYSTEM_USER_ID,
                self._candidate_period(),
            )
        )
        awaiting = await self._count(
            select(func.count(distinct(TaskCandidate.id))).where(
                TaskCandidate.user_id == SYSTEM_USER_ID,
                self._candidate_period(),
                TaskCandidate.status.in_([CandidateStatus.PENDING, CandidateStatus.NOTIFIED]),
                TaskCandidate.task_id.is_(None),
            )
        )
        tasks = await self._count(
            select(func.count(distinct(TaskSource.task_id)))
            .join(Task, Task.id == TaskSource.task_id)
            .join(SourceMessage, SourceMessage.id == TaskSource.source_message_id)
            .where(
                Task.user_id == SYSTEM_USER_ID,
                SourceMessage.source_type == "GMAIL",
                self._task_period(),
                TaskSource.relation == "CREATED_FROM",
            )
        )
        conversion = round(tasks / candidate_count, 4) if candidate_count else 0
        data_quality = await self._data_quality()
        freshness = await self._freshness()
        filters = self._filter_state()

        def metric(
            key: str,
            label: str,
            value: int | float,
            definition: str,
            route: str,
            basis: str,
        ) -> dict[str, Any]:
            return {
                "key": key,
                "label": label,
                "value": value,
                "definition": definition,
                "count_basis": basis,
                "filters": filters,
                "drilldown": {"route": route, "filters": filters},
                "data_quality": data_quality,
            }

        return {
            "generated_at": datetime.now(UTC),
            "period": {
                "from": self.period.start,
                "to": self.period.end,
                "timezone": self.period.timezone,
            },
            "data_freshness": freshness,
            "health": freshness,
            "data_quality": data_quality,
            "metrics": [
                metric(
                    "messages_detected",
                    "Писем обнаружено",
                    detected,
                    "Gmail messages by received_at",
                    "/dashboard/messages",
                    "source_messages.received_at",
                ),
                metric(
                    "messages_processed",
                    "Обработано успешно",
                    processed,
                    "PROCESSED Gmail messages",
                    "/dashboard/messages",
                    "source_messages.received_at + processing_status",
                ),
                metric(
                    "messages_filtered",
                    "Отфильтровано",
                    filtered,
                    "IGNORED Gmail messages",
                    "/dashboard/messages",
                    "source_messages.received_at + processing_status",
                ),
                metric(
                    "messages_failed",
                    "Ошибок обработки",
                    failed,
                    "FAILED Gmail messages",
                    "/dashboard/messages",
                    "source_messages.received_at + processing_status",
                ),
                metric(
                    "candidates",
                    "Candidate найдено",
                    candidate_count,
                    "task_candidates by detected_at",
                    "/dashboard/candidates",
                    "task_candidates.detected_at",
                ),
                metric(
                    "candidates_awaiting",
                    "Ожидают решения",
                    awaiting,
                    "PENDING or NOTIFIED candidates without task",
                    "/dashboard/candidates",
                    "task_candidates.detected_at + status",
                ),
                metric(
                    "tasks_from_gmail",
                    "Создано задач из Gmail",
                    tasks,
                    "distinct CREATED_FROM task links",
                    "/dashboard/tasks",
                    "task_sources.created_at",
                ),
                metric(
                    "task_conversion",
                    "Конверсия в задачи",
                    conversion,
                    "tasks_from_gmail / candidates",
                    "/dashboard/tasks",
                    "derived",
                ),
            ],
            "funnel": [
                self._funnel_node(
                    "detected", "Обнаружено", detected, "/dashboard/messages", data_quality
                ),
                self._funnel_node(
                    "processed", "Обработано", processed, "/dashboard/messages", data_quality
                ),
                self._funnel_node(
                    "filtered", "Отфильтровано", filtered, "/dashboard/messages", data_quality
                ),
                self._funnel_node("failed", "Ошибка", failed, "/dashboard/messages", data_quality),
                self._funnel_node(
                    "candidate", "Candidate", candidate_count, "/dashboard/candidates", data_quality
                ),
                self._funnel_node(
                    "task", "Подтверждено как задача", tasks, "/dashboard/tasks", data_quality
                ),
            ],
        }

    async def timeseries(self) -> dict[str, Any]:
        message_rows = await self.session.execute(
            select(
                func.date(SourceMessage.received_at).label("bucket"),
                func.count(distinct(SourceMessage.id)),
            )
            .where(*self._base_message_conditions())
            .group_by(func.date(SourceMessage.received_at))
        )
        candidate_rows = await self.session.execute(
            select(
                func.date(TaskCandidate.detected_at).label("bucket"),
                func.count(distinct(TaskCandidate.id)),
            )
            .where(TaskCandidate.user_id == SYSTEM_USER_ID, self._candidate_period())
            .group_by(func.date(TaskCandidate.detected_at))
        )
        task_rows = await self.session.execute(
            select(
                func.date(TaskSource.created_at).label("bucket"),
                func.count(distinct(TaskSource.task_id)),
            )
            .join(Task, Task.id == TaskSource.task_id)
            .join(SourceMessage, SourceMessage.id == TaskSource.source_message_id)
            .where(
                Task.user_id == SYSTEM_USER_ID,
                SourceMessage.source_type == "GMAIL",
                TaskSource.relation == "CREATED_FROM",
                self._task_period(),
            )
            .group_by(func.date(TaskSource.created_at))
        )
        buckets: dict[str, dict[str, Any]] = {}
        for name, rows in (
            ("messages", message_rows),
            ("candidates", candidate_rows),
            ("tasks", task_rows),
        ):
            for bucket, count in rows.all():
                key = str(bucket)
                buckets.setdefault(
                    key, {"bucket": key, "messages": 0, "candidates": 0, "tasks": 0}
                )[name] = int(count)
        return {
            "period": {
                "from": self.period.start,
                "to": self.period.end,
                "timezone": self.period.timezone,
            },
            "bucket": "day",
            "points": [buckets[key] for key in sorted(buckets)],
            "data_quality": await self._data_quality(),
        }

    async def messages(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        statement = (
            select(SourceMessage, TaskCandidate, Task)
            .outerjoin(TaskCandidate, TaskCandidate.source_message_id == SourceMessage.id)
            .outerjoin(
                TaskSource,
                and_(
                    TaskSource.source_message_id == SourceMessage.id,
                    TaskSource.relation == "CREATED_FROM",
                ),
            )
            .outerjoin(Task, Task.id == TaskSource.task_id)
            .where(*self._base_message_conditions())
            .order_by(SourceMessage.received_at.desc(), SourceMessage.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(statement)).all()
        return [self._message_row(source, candidate, task) for source, candidate, task in rows]

    async def candidates(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        statement = (
            select(TaskCandidate, SourceMessage, Task)
            .join(SourceMessage, SourceMessage.id == TaskCandidate.source_message_id)
            .outerjoin(Task, Task.id == TaskCandidate.task_id)
            .where(TaskCandidate.user_id == SYSTEM_USER_ID, self._candidate_period())
            .order_by(TaskCandidate.detected_at.desc(), TaskCandidate.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(statement)).all()
        return [self._candidate_row(candidate, source, task) for candidate, source, task in rows]

    async def tasks(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        statement = (
            select(Task, SourceMessage)
            .join(
                TaskSource,
                and_(TaskSource.task_id == Task.id, TaskSource.relation == "CREATED_FROM"),
            )
            .join(SourceMessage, SourceMessage.id == TaskSource.source_message_id)
            .where(
                Task.user_id == SYSTEM_USER_ID,
                SourceMessage.source_type == "GMAIL",
                self._task_period(),
            )
            .order_by(TaskSource.created_at.desc(), Task.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            {
                "id": task.id,
                "title": task.title,
                "task_type": str(task.task_type),
                "status": str(task.status),
                "priority": str(task.priority),
                "created_at": task.created_at,
                "due_at": task.due_at,
                "source_message_id": source.id,
                "source_external_id": source.external_id,
            }
            for task, source in rows
        ]

    async def operations(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        statement = (
            select(IntegrationPollRun, IntegrationAccount)
            .outerjoin(IntegrationAccount, IntegrationAccount.id == IntegrationPollRun.account_id)
            .where(IntegrationPollRun.user_id == SYSTEM_USER_ID)
            .order_by(IntegrationPollRun.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            {
                "id": run.id,
                "provider": run.provider,
                "account_id": run.account_id,
                "account_status": account.status if account else None,
                "trigger": run.trigger,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "status": run.status,
                "duration_seconds": (
                    (run.finished_at - run.started_at).total_seconds()
                    if run.finished_at is not None
                    else None
                ),
                "fetched_count": run.fetched_count,
                "stored_count": run.stored_count,
                "duplicate_count": run.duplicate_count,
                "processed_count": run.processed_count,
                "ignored_count": run.ignored_count,
                "candidate_count": run.candidate_count,
                "failed_count": run.failed_count,
                "notified_count": run.notified_count,
                "error_code": run.error_code,
            }
            for run, account in rows
        ]

    async def candidate_detail(self, candidate_id: UUID) -> dict[str, Any] | None:
        row = await self.session.execute(
            select(TaskCandidate, SourceMessage, Task)
            .join(SourceMessage, SourceMessage.id == TaskCandidate.source_message_id)
            .outerjoin(Task, Task.id == TaskCandidate.task_id)
            .where(TaskCandidate.id == candidate_id, TaskCandidate.user_id == SYSTEM_USER_ID)
        )
        item = row.first()
        if item is None:
            return None
        candidate, source, task = item
        return self._candidate_row(candidate, source, task)

    async def _data_quality(self) -> str:
        poll_count = await self._count(
            select(func.count(IntegrationPollRun.id)).where(
                IntegrationPollRun.user_id == SYSTEM_USER_ID,
                IntegrationPollRun.started_at >= self.period.start,
                IntegrationPollRun.started_at < self.period.end,
            )
        )
        return "COMPLETE" if poll_count else "PARTIAL"

    async def _freshness(self) -> dict[str, Any]:
        account = await self.session.scalar(
            select(IntegrationAccount)
            .where(
                IntegrationAccount.user_id == SYSTEM_USER_ID,
                IntegrationAccount.provider == IntegrationProvider.GMAIL,
            )
            .limit(1)
        )
        last_success = await self.session.scalar(
            select(func.max(IntegrationPollRun.finished_at)).where(
                IntegrationPollRun.user_id == SYSTEM_USER_ID,
                IntegrationPollRun.provider == IntegrationProvider.GMAIL,
                IntegrationPollRun.status.in_([PollRunStatus.SUCCEEDED, PollRunStatus.PARTIAL]),
            )
        )
        last_success = _as_utc(last_success)
        poll_minutes = 15
        if account is not None:
            settings = await self.session.get(UserSettings, account.user_id)
            if settings is not None:
                poll_minutes = settings.gmail_poll_minutes
        age_seconds = (datetime.now(UTC) - last_success).total_seconds() if last_success else None
        state = (
            "UNKNOWN"
            if last_success is None
            else ("STALE" if age_seconds and age_seconds > poll_minutes * 120 else "FRESH")
        )
        return {
            "integration_status": account.status if account else IntegrationStatus.DISCONNECTED,
            "last_successful_poll": last_success,
            "age_seconds": age_seconds,
            "state": state,
        }

    def _filter_state(self) -> dict[str, Any]:
        return {
            "from": self.period.start,
            "to": self.period.end,
            "timezone": self.period.timezone,
        }

    def _funnel_node(
        self, key: str, label: str, value: int, route: str, data_quality: str
    ) -> dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "value": value,
            "count_basis": "dashboard metric definition",
            "filters": self._filter_state(),
            "drilldown": {"route": route, "filters": self._filter_state()},
            "data_quality": data_quality,
        }

    @staticmethod
    def _message_row(
        source: SourceMessage, candidate: TaskCandidate | None, task: Task | None
    ) -> dict[str, Any]:
        gmail_metadata = source.extra.get("gmail")
        filter_decision = (
            gmail_metadata.get("filter_decision") if isinstance(gmail_metadata, dict) else None
        )
        return {
            "id": source.id,
            "external_id": source.external_id,
            "received_at": source.received_at,
            "processed_at": source.processed_at,
            "sender": source.sender_name or source.sender_email or source.sender_external_id,
            "sender_email": source.sender_email,
            "subject": source.subject,
            "source_url": source.source_url,
            "processing_status": source.processing_status,
            "classification": source.classification,
            "confidence": source.confidence,
            "filter_decision": filter_decision,
            "error_code": source.error_code,
            "candidate_id": candidate.id if candidate else None,
            "candidate_status": candidate.status if candidate else None,
            "task_id": task.id if task else None,
            "task_title": task.title if task else None,
        }

    @staticmethod
    def _candidate_row(
        candidate: TaskCandidate, source: SourceMessage, task: Task | None
    ) -> dict[str, Any]:
        return {
            "id": candidate.id,
            "source_message_id": source.id,
            "source_external_id": source.external_id,
            "sender": source.sender_name or source.sender_email or source.sender_external_id,
            "subject": source.subject,
            "source_url": source.source_url,
            "classification": candidate.classification,
            "confidence": candidate.confidence,
            "payload": candidate.payload,
            "status": candidate.status,
            "detected_at": candidate.detected_at,
            "notified_at": candidate.notified_at,
            "decided_at": candidate.decided_at,
            "decision_reason": candidate.decision_reason,
            "notification_error": candidate.notification_error,
            "task_id": task.id if task else candidate.task_id,
            "task_title": task.title if task else None,
        }


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
