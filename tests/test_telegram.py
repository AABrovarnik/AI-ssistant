from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from app.integrations.telegram.bot import TelegramBot
from app.integrations.telegram.client import TelegramClientProtocol
from app.llm.schemas import MessageClassification, SearchDateFilter, SearchFilters, TaskCandidate
from app.tasks.models import Task, TaskEvent, TaskStatus, TaskType


class FakeTelegramClient:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.callbacks: list[tuple[str, str]] = []

    async def get_updates(
        self, offset: int | None = None, timeout: int = 25
    ) -> list[dict[str, Any]]:
        return []

    async def send_message(
        self, chat_id: int, text: str, reply_markup: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        self.messages.append((chat_id, text))
        return {"chat": {"id": chat_id}, "text": text, "reply_markup": reply_markup}

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"chat": {"id": chat_id}, "message_id": message_id, "text": text}

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        self.callbacks.append((callback_query_id, text))

    async def close(self) -> None:
        return None


def make_bot(
    client: FakeTelegramClient, owner_user_id: int = 42, timezone: str = "UTC"
) -> TelegramBot:
    return TelegramBot(
        cast(TelegramClientProtocol, client),
        cast(Any, None),
        owner_user_id,
        timezone=timezone,
    )


@pytest.mark.asyncio
async def test_owner_can_use_help_and_start_commands() -> None:
    client = FakeTelegramClient()
    bot = make_bot(client)

    await bot.handle_update(
        {"message": {"message_id": 1, "from": {"id": 42}, "chat": {"id": 100}, "text": "/start"}}
    )
    await bot.handle_update(
        {"message": {"message_id": 2, "from": {"id": 42}, "chat": {"id": 100}, "text": "/help"}}
    )

    assert client.messages[0][0] == 100
    assert "AI Secretary готов" in client.messages[0][1]
    assert "/new текст" in client.messages[1][1]
    assert "/waiting" in client.messages[1][1]


@pytest.mark.asyncio
async def test_non_owner_is_ignored_and_callback_is_rejected() -> None:
    client = FakeTelegramClient()
    bot = make_bot(client)

    await bot.handle_update(
        {"message": {"from": {"id": 7}, "chat": {"id": 100}, "text": "/help"}}
    )
    await bot.handle_update(
        {
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 7},
                "data": "task:done:00000000-0000-0000-0000-000000000001:1",
            }
        }
    )

    assert client.messages == []
    assert client.callbacks == [("callback-1", "Доступ запрещён")]


@pytest.mark.asyncio
async def test_status_message_gets_safe_preview_without_mutating_tasks() -> None:
    client = FakeTelegramClient()
    bot = make_bot(client)

    await bot._handle_status_message(
        100,
        "Иван прислал договор",
        MessageClassification.TASK_COMPLETE,
    )

    assert "TASK_COMPLETE" in client.messages[0][1]
    assert "Задачи пока не изменены" in client.messages[0][1]


def test_natural_language_search_filters_tasks() -> None:
    tasks = [
        Task(
            title="Получить расчёт от Ивана",
            task_type=TaskType.DELEGATED,
            status=TaskStatus.NEW,
            due_at=datetime.now(UTC) + timedelta(days=1),
        ),
        Task(
            title="Позвонить Сергею",
            task_type=TaskType.MY_TASK,
            status=TaskStatus.NEW,
            due_at=datetime.now(UTC) + timedelta(days=1),
        ),
    ]

    result = TelegramBot._apply_search_filters(
        tasks,
        SearchFilters(
            task_type=[TaskType.DELEGATED],
            assignee_name="Иван",
            date_filter=SearchDateFilter.TOMORROW,
        ),
    )

    assert [task.title for task in result] == ["Получить расчёт от Ивана"]


def test_task_keyboard_contains_phase2_actions() -> None:
    task = Task(id=uuid4(), title="test", idempotency_key="test")
    keyboard = TelegramBot._keyboard([task])
    assert keyboard is not None
    labels = [button["text"] for row in keyboard["inline_keyboard"] for button in row]
    assert labels == [
        "✅ Выполнено",
        "✏️ Название",
        "📅 Срок",
        "🔄 Статус",
        "↔ Перенести",
        "⏳ Жду",
        "❌ Отмена",
        "🔔 Напомнить",
        "📜 История",
    ]


def test_task_list_uses_russian_date_format_without_internal_id() -> None:
    task = Task(
        id=uuid4(),
        title="Подготовить смету",
        status=TaskStatus.NEW,
        due_at=datetime(2026, 8, 12, 15, 0, tzinfo=UTC),
    )

    text = TelegramBot._format_tasks("Задачи", [task])

    assert "12.08.2026 15:00" in text
    assert str(task.id) not in text
    assert "2026-08-12T15:00:00" not in text


def test_task_card_converts_utc_to_configured_timezone() -> None:
    task = Task(
        id=uuid4(),
        title="Позвонить",
        status=TaskStatus.NEW,
        task_type=TaskType.MY_TASK,
        due_at=datetime(2026, 8, 12, 15, 0, tzinfo=UTC),
    )

    text = TelegramBot._format_tasks("Задача", [task], "Europe/Moscow")

    assert "Срок: 12.08.2026 18:00" in text
    assert "Статус: Новая" in text
    assert "Тип: Моя задача" in text


def test_due_and_status_edit_inputs_are_parsed() -> None:
    bot = make_bot(FakeTelegramClient(), timezone="Europe/Moscow")
    due_update = bot._parse_due_update("12.08.2026 18:00")

    assert due_update is not None
    assert due_update.due_at == datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
    assert bot._parse_status("в работе") == TaskStatus.IN_PROGRESS


def test_candidate_uses_russian_date_format() -> None:
    candidate = TaskCandidate(
        task_type=TaskType.MY_TASK,
        title="Подготовить смету",
        due_at=datetime(2026, 8, 12, 15, 0, tzinfo=UTC),
        confidence=0.99,
    )

    text = TelegramBot._format_candidate(candidate)

    assert "Срок: 12.08.2026 15:00" in text


def test_history_is_not_in_card_and_has_explicit_formatter() -> None:
    task_id = uuid4()
    user_id = uuid4()
    task = Task(id=task_id, user_id=user_id, title="Смета", status=TaskStatus.NEW)
    events = [
        TaskEvent(
            task_id=task_id,
            user_id=user_id,
            event_type="STATUS_CHANGED",
            old_value={"status": "NEW"},
            new_value={"status": "DONE"},
            created_at=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
        )
    ]

    card = TelegramBot._format_tasks("Задача", [task])
    history = TelegramBot._format_history(task, events)

    assert "История" not in card
    assert "Статус: Новая → Выполнена" in history
    assert "11.08.2026 12:00" in history


def test_candidate_keyboard_contains_confirmation_actions() -> None:
    keyboard = TelegramBot._candidate_keyboard(uuid4())
    labels = [button["text"] for row in keyboard["inline_keyboard"] for button in row]

    assert labels == ["✅ Создать", "✏️ Изменить", "❌ Игнорировать"]
    assert all(
        button["callback_data"].startswith("candidate:")
        for row in keyboard["inline_keyboard"]
        for button in row
    )
