"""Deterministic read-only daily digest delivery."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tasks.models import (
    DigestDelivery,
    DigestType,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
    UserSettings,
)
from app.tasks.service import TaskService

if TYPE_CHECKING:
    from app.integrations.telegram.client import TelegramClientProtocol

DIGEST_WINDOW = timedelta(minutes=1)


def digest_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [
            [
                {"text": "📌 Сегодня", "callback_data": "digest:today"},
                {"text": "⚠️ Просроченные", "callback_data": "digest:overdue"},
            ],
            [
                {"text": "🤝 Поручения", "callback_data": "digest:delegated"},
                {"text": "⏳ Ожидания", "callback_data": "digest:waiting"},
            ],
        ]
    }


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _local_due_date(task: Task, timezone: str) -> date | None:
    if task.due_date is not None:
        return task.due_date
    if task.due_at is not None:
        return task.due_at.astimezone(_zone(timezone)).date()
    return None


def _is_overdue(task: Task, now: datetime, timezone: str) -> bool:
    if task.due_at is not None and task.due_at < now:
        return True
    due_date = _local_due_date(task, timezone)
    return due_date is not None and due_date < now.astimezone(_zone(timezone)).date()


def _status_label(status: TaskStatus) -> str:
    labels = {
        TaskStatus.NEW: "Новая",
        TaskStatus.UNKNOWN_PARTY: "Исполнитель/отправитель не известен",
        TaskStatus.PLANNED: "Запланирована",
        TaskStatus.IN_PROGRESS: "В работе",
        TaskStatus.WAITING: "Жду",
        TaskStatus.DONE: "Выполнена",
        TaskStatus.OVERDUE: "Просрочена",
        TaskStatus.POSTPONED: "Отложена",
        TaskStatus.ON_HOLD: "На паузе",
        TaskStatus.CANCELLED: "Отменена",
    }
    return labels.get(status, status.value)


def _priority_label(priority: TaskPriority) -> str:
    return priority.value


def _type_label(task_type: TaskType) -> str:
    return {
        TaskType.MY_TASK: "моя",
        TaskType.DELEGATED: "поручение",
        TaskType.AWAITING: "ожидание",
    }.get(task_type, task_type.value)


def _task_line(task: Task, timezone: str) -> str:
    try:
        status = TaskStatus(task.status)
    except ValueError:
        status = TaskStatus.NEW
    try:
        priority = TaskPriority(task.priority)
    except ValueError:
        priority = TaskPriority.P3
    try:
        task_type = TaskType(task.task_type)
    except ValueError:
        task_type = TaskType.MY_TASK

    details = [f"{_status_label(status)}, {_type_label(task_type)}, {_priority_label(priority)}"]
    due_date = _local_due_date(task, timezone)
    if task.due_at is not None:
        details.append(task.due_at.astimezone(_zone(timezone)).strftime("до %d.%m %H:%M"))
    elif due_date is not None:
        details.append(due_date.strftime("до %d.%m"))
    assignee = (task.extra or {}).get("assignee_name")
    if task_type in {TaskType.DELEGATED, TaskType.AWAITING}:
        assignee_label = assignee if isinstance(assignee, str) and assignee else "не указан"
        details.append(f"исполнитель: {assignee_label}")
    return f"• {task.title} ({'; '.join(details)})"


def _unique_tasks(tasks: Iterable[Task]) -> list[Task]:
    result: list[Task] = []
    seen: set[Any] = set()
    for task in tasks:
        if task.id in seen:
            continue
        seen.add(task.id)
        result.append(task)
    return result


def format_morning_digest(
    tasks: list[Task],
    now: datetime,
    timezone: str = "Europe/Moscow",
    digest_time: time = time(7, 0),
) -> str:
    """Build a read-only morning report from already loaded task rows."""

    local_now = now.astimezone(_zone(timezone))
    today = local_now.date()
    active = [
        task
        for task in tasks
        if TaskStatus(task.status) not in {TaskStatus.DONE, TaskStatus.CANCELLED}
    ]
    risks = _unique_tasks(
        task
        for task in active
        if TaskPriority(task.priority) == TaskPriority.P1
        or _is_overdue(task, now, timezone)
        or TaskStatus(task.status) == TaskStatus.UNKNOWN_PARTY
    )[:3]
    today_tasks = [
        task
        for task in active
        if _local_due_date(task, timezone) == today and task not in risks
    ]
    delegated = [
        task
        for task in active
        if TaskType(task.task_type) == TaskType.DELEGATED and task not in risks
    ]
    waiting = [
        task
        for task in active
        if TaskType(task.task_type) == TaskType.AWAITING and task not in risks
    ]

    lines = [
        f"☀️ Утренний отчёт — {local_now.strftime('%d.%m.%Y')} "
        f"{digest_time.strftime('%H:%M')} (МСК)",
        "",
    ]
    if not active:
        lines.append("Активных задач нет.")
    else:
        sections = (
            ("⚠️ Риски", risks),
            ("📌 Сегодня", today_tasks),
            ("🤝 Поручения", delegated),
            ("⏳ Ожидаю", waiting),
        )
        for title, section_tasks in sections:
            if section_tasks:
                lines.append(title)
                lines.extend(_task_line(task, timezone) for task in section_tasks)
                lines.append("")
        if len(lines) == 2:
            lines.append(f"Активных задач: {len(active)}. Сроки не указаны.")
    lines.append("Ответ не требуется — это информационный отчёт.")
    return "\n".join(lines)


def format_evening_review(
    tasks: list[Task],
    now: datetime,
    timezone: str = "Europe/Moscow",
    review_time: time = time(19, 0),
) -> str:
    """Build a read-only list of unresolved items for the end of the day."""

    local_now = now.astimezone(_zone(timezone))
    active = [
        task
        for task in tasks
        if TaskStatus(task.status) not in {TaskStatus.DONE, TaskStatus.CANCELLED}
    ]
    overdue = [task for task in active if _is_overdue(task, now, timezone)]
    due_today = [
        task
        for task in active
        if _local_due_date(task, timezone) == local_now.date() and task not in overdue
    ]
    waiting = [
        task
        for task in active
        if TaskType(task.task_type) == TaskType.AWAITING and task not in overdue + due_today
    ]
    lines = [
        f"🌙 Вечерний обзор — {local_now.strftime('%d.%m.%Y')} "
        f"{review_time.strftime('%H:%M')} (МСК)",
        "",
    ]
    sections = (
        ("⚠️ Просрочено", overdue),
        ("📌 Не закрыто сегодня", due_today),
        ("⏳ Ожидаю", waiting),
    )
    for title, section_tasks in sections:
        if section_tasks:
            lines.append(title)
            lines.extend(_task_line(task, timezone) for task in section_tasks)
            lines.append("")
    if not active:
        lines.append("Незакрытых задач нет — день завершён чисто.")
    elif len(lines) == 2:
        lines.append("Незакрытые задачи есть, но срок не указан.")
        lines.extend(_task_line(task, timezone) for task in active[:10])
    lines.append("Ответ не требуется — это информационный обзор.")
    return "\n".join(lines)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_weekly_review(
    tasks: list[Task],
    now: datetime,
    timezone: str = "Europe/Moscow",
    review_time: time = time(19, 0),
) -> str:
    """Build a read-only seven-day review from task rows."""

    local_now = now.astimezone(_zone(timezone))
    week_start = now - timedelta(days=7)
    completed = [
        task
        for task in tasks
        if TaskStatus(task.status) == TaskStatus.DONE
        and (_as_utc(task.completed_at) or datetime.min.replace(tzinfo=UTC)) >= week_start
    ]
    active = [
        task
        for task in tasks
        if TaskStatus(task.status) not in {TaskStatus.DONE, TaskStatus.CANCELLED}
    ]
    risks = _unique_tasks(
        task
        for task in active
        if _is_overdue(task, now, timezone)
        or TaskStatus(task.status) == TaskStatus.UNKNOWN_PARTY
    )
    delegated = [
        task
        for task in active
        if TaskType(task.task_type) in {TaskType.DELEGATED, TaskType.AWAITING}
        and task not in risks
    ]
    lines = [
        f"📊 Недельный обзор — {local_now.strftime('%d.%m.%Y')} "
        f"{review_time.strftime('%H:%M')} (МСК)",
        f"Период: {(local_now.date() - timedelta(days=6)).strftime('%d.%m')}—"
        f"{local_now.strftime('%d.%m.%Y')}",
        "",
    ]
    sections = (
        ("✅ Завершено", completed),
        ("⚠️ Осталось", risks),
        ("🤝 Поручения и ожидания", delegated),
    )
    for title, section_tasks in sections:
        if section_tasks:
            lines.append(title)
            lines.extend(_task_line(task, timezone) for task in section_tasks[:20])
            lines.append("")
    if not completed and not risks and not delegated:
        lines.append("Изменений и незакрытых задач за неделю нет.")
    lines.append("Ответ не требуется — это информационный обзор.")
    return "\n".join(lines)


def _is_digest_window(now: datetime, digest_time: time, timezone: str) -> bool:
    local_now = now.astimezone(_zone(timezone))
    target = local_now.replace(
        hour=digest_time.hour, minute=digest_time.minute, second=0, microsecond=0
    )
    return target <= local_now < target + DIGEST_WINDOW


async def send_morning_digest_if_due(
    session_factory: async_sessionmaker[AsyncSession],
    client: TelegramClientProtocol,
    chat_id: int,
    timezone: str = "Europe/Moscow",
    now: datetime | None = None,
) -> bool:
    return await send_digest_if_due(
        session_factory, client, chat_id, DigestType.MORNING, timezone, now
    )


async def send_evening_review_if_due(
    session_factory: async_sessionmaker[AsyncSession],
    client: TelegramClientProtocol,
    chat_id: int,
    timezone: str = "Europe/Moscow",
    now: datetime | None = None,
) -> bool:
    return await send_digest_if_due(
        session_factory, client, chat_id, DigestType.EVENING, timezone, now
    )


async def send_weekly_review_if_due(
    session_factory: async_sessionmaker[AsyncSession],
    client: TelegramClientProtocol,
    chat_id: int,
    timezone: str = "Europe/Moscow",
    now: datetime | None = None,
) -> bool:
    return await send_digest_if_due(
        session_factory, client, chat_id, DigestType.WEEKLY, timezone, now
    )


async def send_digest_if_due(
    session_factory: async_sessionmaker[AsyncSession],
    client: TelegramClientProtocol,
    chat_id: int,
    digest_type: DigestType,
    timezone: str = "Europe/Moscow",
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(UTC)
    async with session_factory() as session:
        service = TaskService(session)
        user = await service.ensure_user()
        settings = await session.get(UserSettings, user.id)
        if settings is None:
            return False
        if digest_type == DigestType.MORNING:
            digest_time = settings.morning_digest_time
        else:
            digest_time = settings.evening_digest_time
        local_now = current.astimezone(_zone(timezone))
        if (
            digest_type == DigestType.WEEKLY
            and local_now.isoweekday() != settings.weekly_review_day
        ):
            return False
        if not _is_digest_window(current, digest_time, timezone):
            return False
        digest_date = current.astimezone(_zone(timezone)).date()
        existing = await session.scalar(
            select(DigestDelivery).where(
                DigestDelivery.user_id == user.id,
                DigestDelivery.digest_type == digest_type,
                DigestDelivery.digest_date == digest_date,
            )
        )
        if existing is not None:
            return False

        tasks = await service.list(user_id=user.id, limit=500)
        if digest_type == DigestType.MORNING:
            text = format_morning_digest(tasks, current, timezone, digest_time)
        elif digest_type == DigestType.EVENING:
            text = format_evening_review(tasks, current, timezone, digest_time)
        else:
            text = format_weekly_review(tasks, current, timezone, digest_time)
        delivery = DigestDelivery(
            user_id=user.id,
            digest_type=digest_type,
            digest_date=digest_date,
        )
        session.add(delivery)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return False
        await client.send_message(chat_id, text, digest_keyboard())
        await session.commit()
        return True


async def run_morning_digest_loop(
    session_factory: async_sessionmaker[AsyncSession],
    client: TelegramClientProtocol,
    chat_id: int,
    timezone: str = "Europe/Moscow",
    interval_seconds: int = 30,
) -> None:
    await run_digest_loop(session_factory, client, chat_id, timezone, interval_seconds)


async def run_digest_loop(
    session_factory: async_sessionmaker[AsyncSession],
    client: TelegramClientProtocol,
    chat_id: int,
    timezone: str = "Europe/Moscow",
    interval_seconds: int = 30,
) -> None:
    import asyncio

    while True:
        try:
            await send_morning_digest_if_due(session_factory, client, chat_id, timezone)
            await send_evening_review_if_due(session_factory, client, chat_id, timezone)
            await send_weekly_review_if_due(session_factory, client, chat_id, timezone)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The worker must keep polling Telegram if a digest attempt fails.
            import logging

            logging.getLogger(__name__).exception("digest_delivery_failed")
        await asyncio.sleep(interval_seconds)
