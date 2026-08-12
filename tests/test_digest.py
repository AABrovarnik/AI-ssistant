from datetime import UTC, datetime, time
from typing import Any, cast
from uuid import uuid4

import pytest
from app.db.session import session_factory
from app.integrations.telegram.client import TelegramClientProtocol
from app.jobs.digest import (
    digest_keyboard,
    format_evening_review,
    format_morning_digest,
    format_weekly_review,
    send_evening_review_if_due,
    send_morning_digest_if_due,
    send_weekly_review_if_due,
)
from app.tasks.models import Task, TaskPriority, TaskStatus, TaskType, UserSettings
from app.tasks.schemas import TaskCreate
from app.tasks.service import TaskService


class FakeDigestClient:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(
        self, chat_id: int, text: str, reply_markup: Any = None
    ) -> dict[str, Any]:
        self.messages.append((chat_id, text))
        return {"chat": {"id": chat_id}, "text": text, "reply_markup": reply_markup}


def test_digest_keyboard_links_to_task_lists() -> None:
    buttons = [
        button
        for row in digest_keyboard()["inline_keyboard"]
        for button in row
    ]

    assert [button["callback_data"] for button in buttons] == [
        "digest:today",
        "digest:overdue",
        "digest:delegated",
        "digest:waiting",
    ]


def test_morning_digest_is_read_only_and_uses_moscow_dates() -> None:
    tasks = [
        Task(
            id=uuid4(),
            title="Подготовить смету",
            status=TaskStatus.NEW,
            priority=TaskPriority.P1,
            task_type=TaskType.MY_TASK,
            due_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        )
    ]

    report = format_morning_digest(
        tasks,
        datetime(2026, 8, 12, 4, 0, tzinfo=UTC),
        "Europe/Moscow",
    )

    assert "Утренний отчёт — 12.08.2026 07:00 (МСК)" in report
    assert "⚠️ Риски" in report
    assert "Подготовить смету" in report
    assert "Ответ не требуется" in report
    assert tasks[0].status == TaskStatus.NEW


def test_evening_and_weekly_reviews_include_unresolved_and_completed_tasks() -> None:
    now = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)
    tasks = [
        Task(
            id=uuid4(),
            title="Не закрытая задача",
            status=TaskStatus.IN_PROGRESS,
            task_type=TaskType.MY_TASK,
            due_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        ),
        Task(
            id=uuid4(),
            title="Завершённая задача",
            status=TaskStatus.DONE,
            task_type=TaskType.MY_TASK,
            completed_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
        ),
    ]

    evening = format_evening_review(tasks, now)
    weekly = format_weekly_review(tasks, now)

    assert "Не закрытая задача" in evening
    assert "Завершённая задача" in weekly
    assert "Не закрытая задача" in weekly
    assert "Ответ не требуется" in evening
    assert "Ответ не требуется" in weekly


@pytest.mark.asyncio
async def test_morning_digest_sends_at_moscow_seven_and_deduplicates() -> None:
    client = FakeDigestClient()
    async with session_factory() as session:
        service = TaskService(session)
        user = await service.ensure_user()
        settings = await session.get(UserSettings, user.id)
        assert settings is not None
        settings.morning_digest_time = time(7, 0)
        await session.commit()
        await service.create(
            TaskCreate(
                title="Проверить отчёт",
                task_type=TaskType.AWAITING,
                status=TaskStatus.WAITING,
                idempotency_key=f"digest-task-{uuid4()}",
                user_id=user.id,
            )
        )

    moscow_seven = datetime(2026, 8, 12, 4, 0, tzinfo=UTC)
    first = await send_morning_digest_if_due(
        session_factory, cast(TelegramClientProtocol, client), 42, "Europe/Moscow", moscow_seven
    )
    second = await send_morning_digest_if_due(
        session_factory, cast(TelegramClientProtocol, client), 42, "Europe/Moscow", moscow_seven
    )

    assert first is True
    assert second is False
    assert len(client.messages) == 1
    assert "Проверить отчёт" in client.messages[0][1]


@pytest.mark.asyncio
async def test_morning_digest_does_not_send_outside_configured_window() -> None:
    client = FakeDigestClient()
    before_seven = datetime(2026, 8, 12, 3, 59, 30, tzinfo=UTC)

    sent = await send_morning_digest_if_due(
        session_factory, cast(TelegramClientProtocol, client), 42, "Europe/Moscow", before_seven
    )

    assert sent is False
    assert client.messages == []


@pytest.mark.asyncio
async def test_evening_and_weekly_reviews_use_separate_daily_delivery_keys() -> None:
    client = FakeDigestClient()
    async with session_factory() as session:
        service = TaskService(session)
        user = await service.ensure_user()
        settings = await session.get(UserSettings, user.id)
        assert settings is not None
        settings.evening_digest_time = time(19, 0)
        settings.weekly_review_day = 3  # Wednesday, ISO weekday.
        await session.commit()

    wednesday_nineteen = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)
    evening = await send_evening_review_if_due(
        session_factory,
        cast(TelegramClientProtocol, client),
        42,
        "Europe/Moscow",
        wednesday_nineteen,
    )
    weekly = await send_weekly_review_if_due(
        session_factory,
        cast(TelegramClientProtocol, client),
        42,
        "Europe/Moscow",
        wednesday_nineteen,
    )

    assert evening is True
    assert weekly is True
    assert len(client.messages) == 2
    assert "Вечерний обзор" in client.messages[0][1]
    assert "Недельный обзор" in client.messages[1][1]
