from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

import pytest
from app.integrations.telegram.bot import TelegramBot
from app.integrations.telegram.client import TelegramClientProtocol
from app.tasks.models import Task


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


def make_bot(client: FakeTelegramClient, owner_user_id: int = 42) -> TelegramBot:
    return TelegramBot(cast(TelegramClientProtocol, client), cast(Any, None), owner_user_id)


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


def test_task_keyboard_contains_phase2_actions() -> None:
    task = Task(id=uuid4(), title="test", idempotency_key="test")
    keyboard = TelegramBot._keyboard([task])
    assert keyboard is not None
    labels = [button["text"] for row in keyboard["inline_keyboard"] for button in row]
    assert labels == [
        "✅ Выполнено",
        "✏️ Изменить",
        "↔ Перенести",
        "⏳ Жду",
        "❌ Отмена",
        "🔔 Напомнить",
    ]
