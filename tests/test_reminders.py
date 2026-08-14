from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from app.db.session import session_factory
from app.integrations.telegram.client import TelegramClientProtocol
from app.jobs.reminders import (
    plan_reminders,
    reminder_specs,
    send_due_reminders,
    snooze_task_reminders,
)
from app.tasks.models import Reminder, ReminderStatus, Task, TaskPriority, TaskType
from app.tasks.schemas import TaskCreate
from app.tasks.service import TaskService
from sqlalchemy import select


class FakeReminderClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[str] = []
        self.reply_markups: list[Any] = []

    async def send_message(self, chat_id: int, text: str, **_: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("telegram unavailable")
        self.messages.append(text)
        self.reply_markups.append(_.get("reply_markup"))
        return {"chat": {"id": chat_id}, "text": text}


def test_reminder_policy_is_deterministic_by_priority() -> None:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    task = Task(
        id=uuid4(),
        title="Критичный срок",
        due_at=now + timedelta(days=4),
        priority=TaskPriority.P1,
    )

    specs = reminder_specs(task, now)

    assert [spec.reminder_type for spec in specs] == [
        "PRE_DEADLINE_3D",
        "PRE_DEADLINE_1D",
        "PRE_DEADLINE_3H",
        "DEADLINE",
        "OVERDUE_2H",
    ]
    assert reminder_specs(
        Task(id=uuid4(), title="P4", due_at=task.due_at, priority=TaskPriority.P4), now
    ) == []


def test_awaiting_task_gets_one_idempotent_follow_up_spec() -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    task = Task(
        id=uuid4(),
        title="Получить договор",
        task_type=TaskType.AWAITING,
        due_at=now - timedelta(days=2),
        priority=TaskPriority.P3,
    )

    specs = reminder_specs(task, now)
    follow_ups = [spec for spec in specs if spec.reminder_type == "AWAITING_FOLLOW_UP"]

    assert len(follow_ups) == 1
    assert follow_ups[0].remind_at == now
    assert follow_ups[0].dedupe_key == f"policy:{task.id}:AWAITING_FOLLOW_UP"


@pytest.mark.asyncio
async def test_awaiting_follow_up_delivery_has_safe_actions() -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        await TaskService(session).create(
            TaskCreate(
                title="Получить договор",
                task_type=TaskType.AWAITING,
                due_at=now - timedelta(days=2),
                idempotency_key=f"awaiting-follow-up-{uuid4()}",
            )
        )

    await plan_reminders(session_factory, now)
    client = FakeReminderClient()
    sent = await send_due_reminders(
        session_factory, cast(TelegramClientProtocol, client), 42, now
    )

    follow_up_index = next(
        index for index, text in enumerate(client.messages) if "Пора уточнить результат" in text
    )
    keyboard = client.reply_markups[follow_up_index]
    labels = [
        button["text"]
        for row in keyboard["inline_keyboard"]
        for button in row
    ]

    assert sent == 3
    assert labels == ["✅ Результат получен", "⏰ Напомнить завтра"]


@pytest.mark.asyncio
async def test_planning_is_idempotent_and_creates_policy_records() -> None:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        task = await TaskService(session).create(
            TaskCreate(
                title="Запланировать звонок",
                due_at=now + timedelta(days=2),
                priority=TaskPriority.P1,
                idempotency_key=f"reminder-plan-{uuid4()}",
            )
        )

    first = await plan_reminders(session_factory, now)
    second = await plan_reminders(session_factory, now)

    async with session_factory() as session:
        reminders = list(
            (
                await session.scalars(
                    select(Reminder).where(Reminder.task_id == task.id)
                )
            ).all()
        )
    assert first == 4
    assert second == 0
    assert len(reminders) == 4
    assert all(reminder.status == ReminderStatus.PENDING for reminder in reminders)


@pytest.mark.asyncio
async def test_planning_does_not_reinsert_terminal_policy_reminders() -> None:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        task = await TaskService(session).create(
            TaskCreate(
                title="Не дублировать напоминание",
                due_at=now,
                idempotency_key=f"reminder-terminal-{uuid4()}",
            )
        )

    assert await plan_reminders(session_factory, now) == 2
    async with session_factory() as session:
        deadline = await session.scalar(
            select(Reminder).where(
                Reminder.task_id == task.id, Reminder.reminder_type == "DEADLINE"
            )
        )
        assert deadline is not None
        deadline.status = ReminderStatus.SENT
        await session.commit()

    assert await plan_reminders(session_factory, now) == 0
    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(Reminder).where(Reminder.task_id == task.id)
                )
            ).all()
        )
    assert len(rows) == 2
    assert sum(row.reminder_type == "DEADLINE" for row in rows) == 1


@pytest.mark.asyncio
async def test_planning_repairs_stale_pending_policy_schedule() -> None:
    now = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)
    due_at = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        task = await TaskService(session).create(
            TaskCreate(
                title="Исправить старое напоминание",
                due_at=due_at,
                idempotency_key=f"reminder-reschedule-{uuid4()}",
            )
        )
        stale = Reminder(
            task_id=task.id,
            user_id=task.user_id,
            remind_at=now,
            reminder_type="OVERDUE_1D",
            dedupe_key=f"policy:{task.id}:OVERDUE_1D",
            extra={"policy": True},
        )
        session.add(stale)
        await session.commit()

    assert await plan_reminders(session_factory, now) == 0

    async with session_factory() as session:
        reminder = await session.scalar(
            select(Reminder).where(
                Reminder.task_id == task.id,
                Reminder.reminder_type == "OVERDUE_1D",
            )
        )
    assert reminder is not None
    assert reminder.remind_at.replace(tzinfo=UTC) == datetime(
        2026, 8, 13, 12, 0, tzinfo=UTC
    )


@pytest.mark.asyncio
async def test_due_reminder_respects_quiet_hours_and_sends_afterward() -> None:
    now = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)  # 01:00 Europe/Moscow.
    async with session_factory() as session:
        task = await TaskService(session).create(
            TaskCreate(
                title="Тихое напоминание",
                due_at=now,
                idempotency_key=f"reminder-quiet-{uuid4()}",
            )
        )
    await plan_reminders(session_factory, now)
    client = FakeReminderClient()

    sent = await send_due_reminders(
        session_factory,
        cast(TelegramClientProtocol, client),
        42,
        now,
    )

    async with session_factory() as session:
        reminder = await session.scalar(
            select(Reminder).where(
                Reminder.task_id == task.id, Reminder.reminder_type == "DEADLINE"
            )
        )
    assert sent == 0
    assert client.messages == []
    assert reminder is not None
    assert reminder.status == ReminderStatus.PENDING
    assert reminder.remind_at.replace(tzinfo=UTC) == datetime(2026, 8, 13, 4, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_due_reminder_sends_and_failed_delivery_is_retried() -> None:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        task = await TaskService(session).create(
            TaskCreate(
                title="Отправить напоминание",
                due_at=now,
                idempotency_key=f"reminder-send-{uuid4()}",
            )
        )
    await plan_reminders(session_factory, now)

    failed_client = FakeReminderClient(fail=True)
    sent_before_retry = await send_due_reminders(
        session_factory, cast(TelegramClientProtocol, failed_client), 42, now
    )

    async with session_factory() as session:
        deadline = await session.scalar(
            select(Reminder).where(
                Reminder.task_id == task.id, Reminder.reminder_type == "DEADLINE"
            )
        )
    assert sent_before_retry == 0
    assert deadline is not None
    assert deadline.status == ReminderStatus.RETRY
    assert deadline.attempt_count == 1

    ok_client = FakeReminderClient()
    sent = await send_due_reminders(
        session_factory,
        cast(TelegramClientProtocol, ok_client),
        42,
        now + timedelta(minutes=5),
    )

    async with session_factory() as session:
        deadline = await session.scalar(
            select(Reminder).where(
                Reminder.task_id == task.id, Reminder.reminder_type == "DEADLINE"
            )
        )
    assert sent == 1
    assert len(ok_client.messages) == 1
    assert deadline is not None
    assert deadline.status == ReminderStatus.SENT


@pytest.mark.asyncio
async def test_snooze_moves_pending_reminders() -> None:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        task = await TaskService(session).create(
            TaskCreate(
                title="Отложить напоминание",
                due_at=now,
                idempotency_key=f"reminder-snooze-{uuid4()}",
            )
        )
    await plan_reminders(session_factory, now)
    until = now + timedelta(hours=2)
    async with session_factory() as session:
        moved = await snooze_task_reminders(session, task.id, until)

    assert moved == 2
    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(Reminder).where(Reminder.task_id == task.id)
                )
            ).all()
        )
    assert all(row.remind_at.replace(tzinfo=UTC) == until for row in rows)
