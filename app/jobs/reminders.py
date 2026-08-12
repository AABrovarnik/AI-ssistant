"""Deterministic reminder policy, scheduling and delivery."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tasks.models import Reminder, ReminderStatus, Task, TaskPriority, TaskStatus, UserSettings
from app.tasks.service import TaskService

if TYPE_CHECKING:
    from app.integrations.telegram.client import TelegramClientProtocol

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))


@dataclass(frozen=True)
class ReminderSpec:
    remind_at: datetime
    reminder_type: str
    dedupe_key: str


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _active(task: Task) -> bool:
    status = TaskStatus(task.status or TaskStatus.NEW)
    return status not in {TaskStatus.DONE, TaskStatus.CANCELLED}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _policy_offsets(priority: TaskPriority) -> tuple[tuple[str, timedelta], ...]:
    if priority == TaskPriority.P1:
        return (
            ("PRE_DEADLINE_3D", -timedelta(days=3)),
            ("PRE_DEADLINE_1D", -timedelta(days=1)),
            ("PRE_DEADLINE_3H", -timedelta(hours=3)),
            ("DEADLINE", timedelta(0)),
            ("OVERDUE_2H", timedelta(hours=2)),
        )
    if priority == TaskPriority.P2:
        return (
            ("PRE_DEADLINE_1D", -timedelta(days=1)),
            ("DEADLINE", timedelta(0)),
            ("OVERDUE", timedelta(hours=2)),
        )
    if priority == TaskPriority.P3:
        return (
            ("DEADLINE", timedelta(0)),
            ("OVERDUE_1D", timedelta(days=1)),
        )
    return ()


def reminder_specs(
    task: Task, now: datetime, timezone: str = "Europe/Moscow"
) -> list[ReminderSpec]:
    """Return stable reminder records for the current task version."""

    if not _active(task) or task.due_at is None:
        return []
    try:
        priority = TaskPriority(task.priority or TaskPriority.P3)
    except ValueError:
        priority = TaskPriority.P3
    if priority == TaskPriority.P4:
        return []

    due_at = _as_utc(task.due_at)
    specs: list[ReminderSpec] = []
    for reminder_type, offset in _policy_offsets(priority):
        remind_at = due_at + offset
        if remind_at < now - timedelta(minutes=1) and reminder_type not in {
            "OVERDUE",
            "OVERDUE_1D",
        }:
            continue
        specs.append(
            ReminderSpec(
                remind_at=max(remind_at, now),
                reminder_type=reminder_type,
                dedupe_key=f"policy:{task.id}:{reminder_type}",
            )
        )

    local_today = now.astimezone(_zone(timezone)).date()
    due_local_date = due_at.astimezone(_zone(timezone)).date()
    daily_start = due_local_date + (
        timedelta(days=1) if priority == TaskPriority.P3 else timedelta(0)
    )
    if local_today > daily_start:
        specs.append(
            ReminderSpec(
                remind_at=now,
                reminder_type="OVERDUE_DAILY",
                dedupe_key=f"policy:{task.id}:OVERDUE_DAILY:{local_today.isoformat()}",
            )
        )
    return specs


async def plan_reminders(
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
    timezone: str = "Europe/Moscow",
) -> int:
    """Synchronize deterministic policy reminders and return created count."""

    current = now or datetime.now(UTC)
    created = 0
    async with session_factory() as session:
        service = TaskService(session)
        user = await service.ensure_user()
        tasks = await service.list(user_id=user.id, limit=500)
        for task in tasks:
            specs = reminder_specs(task, current, timezone)
            wanted = {spec.dedupe_key: spec for spec in specs}
            # Include terminal rows in the lookup.  The dedupe key is unique for
            # the lifetime of a policy reminder, so filtering to PENDING/RETRY
            # would make a later planner run try to insert a key belonging to a
            # SENT, FAILED, or CANCELLED row and crash on uq_reminders_dedupe.
            existing_rows = list(
                (
                    await session.scalars(
                        select(Reminder).where(
                            Reminder.task_id == task.id,
                            Reminder.dedupe_key.like(f"policy:{task.id}:%"),
                        )
                    )
                ).all()
            )
            existing_by_key = {
                str(row.dedupe_key): row for row in existing_rows if row.dedupe_key
            }
            for row in existing_rows:
                if row.dedupe_key not in wanted:
                    if row.status in {ReminderStatus.PENDING, ReminderStatus.RETRY}:
                        row.status = ReminderStatus.CANCELLED
            for spec in wanted.values():
                existing = existing_by_key.get(spec.dedupe_key)
                if existing is not None:
                    # A cancelled policy can become relevant again after a task
                    # edit. Reuse its unique row instead of inserting a duplicate.
                    if existing.status == ReminderStatus.CANCELLED:
                        existing.status = ReminderStatus.PENDING
                        existing.remind_at = spec.remind_at
                        existing.reminder_type = spec.reminder_type
                        existing.last_error = None
                    continue
                values = {
                    "task_id": task.id,
                    "user_id": task.user_id,
                    "remind_at": spec.remind_at,
                    "reminder_type": spec.reminder_type,
                    "dedupe_key": spec.dedupe_key,
                    "extra": {"policy": True},
                }
                if session.bind is not None and session.bind.dialect.name == "postgresql":
                    result = await session.execute(
                        pg_insert(Reminder)
                        .values(values)
                        .on_conflict_do_nothing(index_elements=[Reminder.dedupe_key])
                    )
                    created += int(getattr(result, "rowcount", 0) or 0)
                else:
                    session.add(Reminder(**values))
                    created += 1
        await session.commit()
    return created


def _is_quiet_hours(local_time: time, start: time, end: time) -> bool:
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def _quiet_hours_end(now: datetime, settings: UserSettings, timezone: str) -> datetime:
    zone = _zone(timezone)
    local_now = now.astimezone(zone)
    end = settings.quiet_hours_end
    target = local_now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if target <= local_now:
        target += timedelta(days=1)
    return target.astimezone(UTC)


def reminder_text(task: Task, reminder: Reminder, timezone: str = "Europe/Moscow") -> str:
    due = ""
    if task.due_at is not None:
        due = _as_utc(task.due_at).astimezone(_zone(timezone)).strftime(
            "\nСрок: %d.%m.%Y %H:%M"
        )
    return f"🔔 Напоминание\n{task.title}\nТип: {reminder.reminder_type}{due}"


async def send_due_reminders(
    session_factory: async_sessionmaker[AsyncSession],
    client: TelegramClientProtocol,
    chat_id: int,
    now: datetime | None = None,
    timezone: str = "Europe/Moscow",
    limit: int = 50,
) -> int:
    """Deliver due reminders, deferring quiet-hour records and retrying failures."""

    current = now or datetime.now(UTC)
    sent = 0
    async with session_factory() as session:
        user = await TaskService(session).ensure_user()
        settings = await session.get(UserSettings, user.id)
        if settings is None:
            return 0
        reminders = list(
            (
                await session.scalars(
                    select(Reminder)
                    .where(
                        Reminder.user_id == user.id,
                        Reminder.status.in_([ReminderStatus.PENDING, ReminderStatus.RETRY]),
                        Reminder.remind_at <= current,
                    )
                    .order_by(Reminder.remind_at)
                    .limit(limit)
                )
            ).all()
        )
        for reminder in reminders:
            local_now = current.astimezone(_zone(timezone))
            if _is_quiet_hours(
                local_now.time(), settings.quiet_hours_start, settings.quiet_hours_end
            ):
                reminder.remind_at = _quiet_hours_end(current, settings, timezone)
                reminder.status = ReminderStatus.PENDING
                continue
            task = await session.get(Task, reminder.task_id)
            if task is None or not _active(task):
                reminder.status = ReminderStatus.CANCELLED
                continue
            try:
                await client.send_message(chat_id, reminder_text(task, reminder, timezone))
            except Exception as exc:
                reminder.attempt_count += 1
                reminder.last_error = str(exc)[:2000]
                if reminder.attempt_count >= MAX_ATTEMPTS:
                    reminder.status = ReminderStatus.FAILED
                else:
                    reminder.status = ReminderStatus.RETRY
                    retry_index = min(reminder.attempt_count - 1, len(RETRY_DELAYS) - 1)
                    reminder.remind_at = current + RETRY_DELAYS[retry_index]
                continue
            reminder.status = ReminderStatus.SENT
            reminder.sent_at = current
            reminder.attempt_count += 1
            task.last_reminded_at = current
            sent += 1
        await session.commit()
    return sent


async def create_manual_reminder(
    session: AsyncSession,
    task: Task,
    remind_at: datetime,
) -> Reminder:
    key = f"manual:{task.id}:{remind_at.isoformat()}"
    existing = await session.scalar(select(Reminder).where(Reminder.dedupe_key == key))
    if existing is not None:
        return existing
    reminder = Reminder(
        task_id=task.id,
        user_id=task.user_id,
        remind_at=remind_at,
        reminder_type="STATUS_CHECK",
        dedupe_key=key,
        extra={"manual": True},
    )
    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return reminder


async def snooze_task_reminders(
    session: AsyncSession,
    task_id: Any,
    until: datetime,
) -> int:
    rows = list(
        (
            await session.scalars(
                select(Reminder).where(
                    Reminder.task_id == task_id,
                    Reminder.status.in_([ReminderStatus.PENDING, ReminderStatus.RETRY]),
                )
            )
        ).all()
    )
    for reminder in rows:
        reminder.remind_at = until
        reminder.status = ReminderStatus.PENDING
        reminder.last_error = None
    await session.commit()
    return len(rows)


async def run_reminder_loop(
    session_factory: async_sessionmaker[AsyncSession],
    client: TelegramClientProtocol,
    chat_id: int,
    timezone: str = "Europe/Moscow",
    interval_seconds: int = 30,
) -> None:
    while True:
        try:
            await plan_reminders(session_factory, timezone=timezone)
            await send_due_reminders(session_factory, client, chat_id, timezone=timezone)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reminder_engine_failed")
        await asyncio.sleep(interval_seconds)
