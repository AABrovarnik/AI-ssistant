"""Telegram commands and callbacks for Phase 2.

The bot talks to the TaskService directly. This keeps Telegram independent from
the REST transport and makes all handlers testable without Telegram or an LLM.
"""

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.telegram.client import (
    TelegramAuthenticationError,
    TelegramClientProtocol,
)
from app.llm.provider import LLMProviderError
from app.llm.schemas import (
    MessageClassification,
    SearchDateFilter,
    SearchFilters,
    TaskCandidate,
)
from app.llm.service import LLMParseError, LLMService
from app.tasks.models import ProcessingStatus, Reminder, Task, TaskStatus, TaskType
from app.tasks.schemas import SourceMessageCreate, TaskCreate, TaskUpdate
from app.tasks.service import (
    InvalidTaskTransitionError,
    TaskNotFoundError,
    TaskService,
    VersionConflictError,
)

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(
        self,
        client: TelegramClientProtocol,
        session_factory: async_sessionmaker[AsyncSession],
        owner_user_id: int,
        llm_service: LLMService | None = None,
    ) -> None:
        self.client = client
        self.session_factory = session_factory
        self.owner_user_id = owner_user_id
        self.llm_service = llm_service

    async def handle_update(self, update: Mapping[str, Any]) -> None:
        message = update.get("message")
        callback = update.get("callback_query")
        actor_id = self._actor_id(message, callback)
        if actor_id != self.owner_user_id:
            if callback:
                await self.client.answer_callback_query(
                    str(callback.get("id", "")), "Доступ запрещён"
                )
            return
        if callback:
            await self._handle_callback(callback)
        elif message:
            await self._handle_message(message)

    async def run_polling(self, stop_event: asyncio.Event | None = None) -> None:
        offset: int | None = None
        while stop_event is None or not stop_event.is_set():
            try:
                updates = await self.client.get_updates(offset=offset)
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    await self.handle_update(update)
            except TelegramAuthenticationError:
                logger.error("telegram_authentication_failed")
                return
            except Exception:
                logger.exception("telegram_polling_error")
                await asyncio.sleep(5)

    async def _handle_message(self, message: Mapping[str, Any]) -> None:
        chat_id = self._chat_id(message)
        text = str(message.get("text", "")).strip()
        if not text:
            await self.client.send_message(chat_id, "Используй /help для списка команд.")
            return
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        if command == "/start":
            await self.client.send_message(
                chat_id,
                "AI Secretary готов. Управляй задачами командами или кнопками.\n/help — помощь",
            )
        elif command == "/help":
            await self.client.send_message(chat_id, self._help_text())
        elif command == "/new":
            await self._new_task(chat_id, argument.strip(), message)
        elif command in {"/today", "/week", "/overdue", "/delegated", "/waiting"}:
            async with self.session_factory() as session:
                service = TaskService(session)
                if command == "/overdue":
                    tasks = await service.list_overdue()
                    title = "Просроченные задачи"
                else:
                    now = datetime.now(UTC)
                    if command == "/today":
                        tasks = await service.list(
                            due_from=now.replace(hour=0, minute=0, second=0, microsecond=0),
                            due_to=now + timedelta(days=1),
                        )
                        title = "Задачи на сегодня"
                    elif command == "/week":
                        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                        tasks = await service.list(due_from=start, due_to=start + timedelta(days=7))
                        title = "Задачи на неделю"
                    elif command == "/delegated":
                        tasks = await service.list(task_type=TaskType.DELEGATED)
                        title = "Делегированные задачи"
                    else:
                        tasks = await service.list(task_type=TaskType.AWAITING)
                        title = "Ожидаемые результаты"
                await self.client.send_message(
                    chat_id, self._format_tasks(title, tasks), self._keyboard(tasks)
                )
        elif command == "/search":
            async with self.session_factory() as session:
                tasks = await TaskService(session).list(query=argument.strip())
            await self.client.send_message(
                chat_id, self._format_tasks("Результаты поиска", tasks), self._keyboard(tasks)
            )
        elif command == "/settings":
            async with self.session_factory() as session:
                user = await TaskService(session).ensure_user()
            await self.client.send_message(
                chat_id,
                f"Настройки\nЧасовой пояс: {user.timezone}\nЯзык: {user.language}\n"
                "Дайджесты и напоминания настраиваются здесь в следующих итерациях.",
            )
        elif command == "/edit":
            await self._edit_task(chat_id, argument)
        elif not command.startswith("/"):
            await self._handle_natural_language(chat_id, text, message)
        else:
            await self.client.send_message(chat_id, "Неизвестная команда. Используй /help.")

    async def _handle_natural_language(
        self, chat_id: int, text: str, message: Mapping[str, Any]
    ) -> None:
        if self.llm_service is None:
            await self.client.send_message(
                chat_id,
                "Свободный текст пока недоступен. Используй /help или /new текст задачи.",
            )
            return
        try:
            parsed = await self.llm_service.parse_message(text)
        except (LLMParseError, LLMProviderError):
            logger.exception("telegram_llm_parse_failed")
            await self.client.send_message(
                chat_id,
                "Не удалось разобрать сообщение. Попробуй сформулировать задачу точнее "
                "или используй /new текст задачи.",
            )
            return

        classification = parsed.classification
        candidate = parsed.extraction.candidate if parsed.extraction else None
        if candidate is not None and classification.classification != MessageClassification.UNCLEAR:
            message_id = str(message.get("message_id", "unknown"))
            sender = message.get("from") or {}
            sender_name = (
                str(sender.get("username") or sender.get("first_name") or "owner")
                if isinstance(sender, Mapping)
                else "owner"
            )
            async with self.session_factory() as session:
                service = TaskService(session)
                source = await service.create_source_message(
                    SourceMessageCreate(
                        source_type="TELEGRAM",
                        external_id=f"{self.owner_user_id}:{message_id}",
                        sender_external_id=str(self.owner_user_id),
                        sender_name=sender_name,
                        text=text,
                    )
                )
                source = await service.save_source_candidate(
                    source.id,
                    classification.classification.value,
                    classification.confidence,
                    candidate.model_dump(mode="json"),
                )
            await self.client.send_message(
                chat_id,
                self._format_candidate(candidate),
                self._candidate_keyboard(source.id),
            )
            return
        if classification.classification in {
            MessageClassification.TASK_COMPLETE,
            MessageClassification.STATUS_UPDATE,
        }:
            await self._handle_status_message(chat_id, text, classification.classification)
            return
        if classification.classification == MessageClassification.INFORMATION:
            await self._handle_search_message(chat_id, text)
            return
        if classification.classification == MessageClassification.UNCLEAR:
            await self.client.send_message(
                chat_id,
                "Не уверен, что правильно понял сообщение. Уточни, что нужно сделать, "
                "для кого и к какому сроку.",
            )
            return
        await self.client.send_message(
            chat_id,
            f"Сообщение распознано как {classification.classification.value.lower()}, "
            "но подходящий сценарий ещё не подключён.",
        )

    async def _handle_status_message(
        self, chat_id: int, text: str, classification: MessageClassification
    ) -> None:
        action = (
            "завершить связанную задачу"
            if classification == MessageClassification.TASK_COMPLETE
            else "обновить статус или срок связанной задачи"
        )
        await self.client.send_message(
            chat_id,
            "Статусное сообщение распознано\n"
            f"Событие: {classification.value}\n"
            f"Предлагаемое действие: {action}\n"
            f"Источник: {text}\n\n"
            "Задачи пока не изменены автоматически. На Phase 4 добавим выбор "
            "связанной задачи и подтверждение.",
        )

    async def _handle_search_message(self, chat_id: int, text: str) -> None:
        if self.llm_service is None:
            return
        try:
            parsed = await self.llm_service.parse_search(text)
        except (LLMParseError, LLMProviderError):
            logger.exception("telegram_llm_search_parse_failed")
            await self.client.send_message(
                chat_id,
                "Не удалось разобрать поисковый запрос. Попробуй уточнить имя или срок.",
            )
            return
        async with self.session_factory() as session:
            tasks = await TaskService(session).list(limit=100)
        tasks = self._apply_search_filters(tasks, parsed.filters)
        await self.client.send_message(
            chat_id,
            self._format_tasks("Результаты поиска", tasks),
            self._keyboard(tasks),
        )

    @classmethod
    def _apply_search_filters(cls, tasks: list[Task], filters: SearchFilters) -> list[Task]:
        filtered: list[Task] = []
        now = datetime.now(UTC)
        for task in tasks:
            task_type = TaskType(task.task_type)
            status = TaskStatus(task.status)
            priority = str(task.priority)
            if filters.task_type and task_type not in filters.task_type:
                continue
            if filters.status and status not in filters.status:
                continue
            if filters.exclude_status and status in filters.exclude_status:
                continue
            if filters.priority and priority not in {item.value for item in filters.priority}:
                continue
            searchable = f"{task.title} {task.description or ''}".casefold()
            if filters.assignee_name and filters.assignee_name.casefold() not in searchable:
                continue
            if filters.text_query and filters.text_query.casefold() not in searchable:
                continue
            if not cls._matches_date_filter(task, filters.date_filter, now):
                continue
            if filters.overdue_days_min is not None:
                overdue_cutoff = now - timedelta(days=filters.overdue_days_min)
                if task.due_at is None or task.due_at > overdue_cutoff:
                    continue
            filtered.append(task)

        if filters.sort == "DUE_ASC":
            filtered.sort(key=lambda item: item.due_at or datetime.max.replace(tzinfo=UTC))
        elif filters.sort == "DUE_DESC":
            filtered.sort(
                key=lambda item: item.due_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
        return filtered[: filters.limit]

    @staticmethod
    def _matches_date_filter(task: Task, date_filter: SearchDateFilter, now: datetime) -> bool:
        if date_filter == SearchDateFilter.NONE:
            return True
        if task.due_at is None:
            return False
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if date_filter == SearchDateFilter.TODAY:
            end = start + timedelta(days=1)
        elif date_filter == SearchDateFilter.TOMORROW:
            start += timedelta(days=1)
            end = start + timedelta(days=1)
        elif date_filter == SearchDateFilter.THIS_WEEK:
            end = start + timedelta(days=7)
        elif date_filter == SearchDateFilter.NEXT_WEEK:
            start += timedelta(days=7)
            end = start + timedelta(days=7)
        else:
            next_month = (start.month % 12) + 1
            year = start.year + (1 if start.month == 12 else 0)
            end = start.replace(year=year, month=next_month, day=1)
        return start <= task.due_at < end

    @staticmethod
    def _format_candidate(candidate: TaskCandidate) -> str:
        lines = [
            "Кандидат задачи",
            f"Тип: {candidate.task_type.value}",
            f"Заголовок: {candidate.title}",
            f"Приоритет: {candidate.priority.value}",
            f"Уверенность: {candidate.confidence:.0%}",
        ]
        if candidate.assignee_name:
            lines.append(f"Исполнитель: {candidate.assignee_name}")
        if candidate.due_at:
            lines.append(f"Срок: {candidate.due_at.isoformat()}")
        elif candidate.due_date:
            lines.append(f"Срок: {candidate.due_date.isoformat()}")
        lines.append("\nЗадача пока не создана — выбери действие ниже.")
        return "\n".join(lines)

    @staticmethod
    def _candidate_keyboard(source_id: UUID) -> dict[str, Any]:
        prefix = f"candidate:{{action}}:{source_id}"
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ Создать", "callback_data": prefix.format(action="create")},
                    {"text": "✏️ Изменить", "callback_data": prefix.format(action="edit")},
                    {"text": "❌ Игнорировать", "callback_data": prefix.format(action="ignore")},
                ]
            ]
        }

    async def _new_task(
        self, chat_id: int, title: str, message: Mapping[str, Any]
    ) -> None:
        if not title:
            await self.client.send_message(chat_id, "Формат: /new текст задачи")
            return
        update_id = str(message.get("message_id", "new"))
        async with self.session_factory() as session:
            task = await TaskService(session).create(
                TaskCreate(
                    title=title,
                    task_type=TaskType.MY_TASK,
                    source_type="TELEGRAM",
                    source_id=update_id,
                    idempotency_key=f"telegram:new:{self.owner_user_id}:{update_id}",
                )
            )
        await self.client.send_message(
            chat_id,
            f"Создано: {task.title}\nСтатус: {self._status(task)}",
            self._keyboard([task]),
        )

    async def _edit_task(self, chat_id: int, argument: str) -> None:
        task_id, _, title = argument.partition(" ")
        if not title:
            await self.client.send_message(chat_id, "Формат: /edit TASK_ID новый заголовок")
            return
        try:
            parsed_id = UUID(task_id)
        except ValueError:
            await self.client.send_message(chat_id, "Некорректный TASK_ID.")
            return
        async with self.session_factory() as session:
            service = TaskService(session)
            try:
                task = await service.get(parsed_id)
                task = await service.update(
                    parsed_id,
                    TaskUpdate(version=task.version, title=title),
                    f"telegram:edit:{self.owner_user_id}:{parsed_id}:{task.version}",
                )
            except (TaskNotFoundError, VersionConflictError) as exc:
                await self.client.send_message(chat_id, f"Не удалось изменить задачу: {exc}")
                return
        await self.client.send_message(chat_id, f"Изменено: {task.title}", self._keyboard([task]))

    async def _handle_callback(self, callback: Mapping[str, Any]) -> None:
        callback_id = str(callback.get("id", ""))
        data = str(callback.get("data", ""))
        message = callback.get("message") or {}
        chat_id = self._chat_id(message)
        parts = data.split(":")
        if len(parts) == 3 and parts[0] == "candidate":
            await self._handle_candidate_callback(callback, parts[1], parts[2])
            return
        if len(parts) != 4 or parts[0] != "task":
            await self.client.answer_callback_query(callback_id, "Неизвестное действие")
            return
        action, task_id_text, version_text = parts[1:]
        try:
            task_id = UUID(task_id_text)
            version = int(version_text)
        except ValueError:
            await self.client.answer_callback_query(callback_id, "Некорректная задача")
            return
        operation_key = f"telegram:callback:{callback_id}"
        try:
            async with self.session_factory() as session:
                service = TaskService(session)
                if action == "done":
                    task = await service.complete(task_id, operation_key, version)
                    notice = "Выполнено"
                elif action == "waiting":
                    task = await service.update(
                        task_id,
                        TaskUpdate(version=version, status=TaskStatus.WAITING),
                        operation_key,
                    )
                    notice = "Переведено в ожидание"
                elif action == "postpone":
                    task = await service.postpone(
                        task_id,
                        datetime.now(UTC) + timedelta(days=1),
                        version,
                        operation_key,
                    )
                    notice = "Перенесено на завтра"
                elif action == "cancel":
                    task = await service.cancel(task_id, version, operation_key)
                    notice = "Отменено"
                elif action == "remind":
                    task = await service.get(task_id)
                    session.add(
                        Reminder(
                            task_id=task.id,
                            user_id=task.user_id,
                            remind_at=datetime.now(UTC) + timedelta(hours=1),
                            reminder_type="STATUS_CHECK",
                            dedupe_key=f"telegram:remind:{task.id}:{task.version}",
                        )
                    )
                    await session.commit()
                    notice = "Напоминание создано на час"
                elif action == "edit":
                    await self.client.answer_callback_query(
                        callback_id,
                        f"Используй /edit {task_id} новый текст",
                    )
                    return
                else:
                    await self.client.answer_callback_query(callback_id, "Неизвестное действие")
                    return
        except (TaskNotFoundError, VersionConflictError, InvalidTaskTransitionError) as exc:
            await self.client.answer_callback_query(callback_id, f"Не выполнено: {exc}")
            return
        await self.client.answer_callback_query(callback_id, notice)
        message_id = message.get("message_id")
        if isinstance(message_id, int):
            await self.client.edit_message_text(
                chat_id,
                message_id,
                self._format_tasks("Задача обновлена", [task]),
                self._keyboard([task]),
            )

    async def _handle_candidate_callback(
        self, callback: Mapping[str, Any], action: str, source_id_text: str
    ) -> None:
        callback_id = str(callback.get("id", ""))
        message = callback.get("message") or {}
        chat_id = self._chat_id(message)
        try:
            source_id = UUID(source_id_text)
        except ValueError:
            await self.client.answer_callback_query(callback_id, "Некорректный кандидат")
            return
        try:
            async with self.session_factory() as session:
                service = TaskService(session)
                source = await service.get_source_message(source_id)
                if action == "ignore":
                    source.processing_status = ProcessingStatus.IGNORED
                    await session.commit()
                    notice = "Кандидат проигнорирован"
                    task = None
                elif action == "edit":
                    await self.client.answer_callback_query(
                        callback_id,
                        "Отправь исправленный вариант отдельным сообщением",
                    )
                    return
                elif action == "create":
                    raw_candidate = source.extra.get("candidate")
                    if not isinstance(raw_candidate, dict):
                        raise ValueError("candidate data is missing")
                    candidate = TaskCandidate.model_validate(raw_candidate)
                    task = await service.create(
                        TaskCreate(
                            title=candidate.title,
                            description=candidate.description,
                            task_type=candidate.task_type,
                            priority=candidate.priority,
                            due_at=candidate.due_at,
                            due_date=candidate.due_date,
                            due_precision=candidate.due_precision,
                            source_type=source.source_type,
                            source_id=source.external_id,
                            source_message_id=source.id,
                            confidence=candidate.confidence,
                            user_id=source.user_id,
                            source="telegram_llm",
                            idempotency_key=f"telegram:candidate:create:{source.id}",
                            extra={
                                "assignee_name": candidate.assignee_name,
                                "evidence": candidate.evidence,
                            },
                        )
                    )
                    source.extra = {**source.extra, "confirmed": True}
                    await session.commit()
                    notice = "Задача создана"
                else:
                    await self.client.answer_callback_query(callback_id, "Неизвестное действие")
                    return
        except (TaskNotFoundError, ValueError) as exc:
            await self.client.answer_callback_query(callback_id, f"Не выполнено: {exc}")
            return

        await self.client.answer_callback_query(callback_id, notice)
        message_id = message.get("message_id")
        if not isinstance(message_id, int):
            return
        if task is None:
            await self.client.edit_message_text(chat_id, message_id, "Кандидат проигнорирован")
        else:
            await self.client.edit_message_text(
                chat_id,
                message_id,
                self._format_tasks("Задача создана", [task]),
                self._keyboard([task]),
            )

    @staticmethod
    def _actor_id(message: Any, callback: Any) -> int | None:
        source = callback.get("from") if callback else message.get("from") if message else None
        actor_id = source.get("id") if isinstance(source, Mapping) else None
        return actor_id if isinstance(actor_id, int) else None

    @staticmethod
    def _chat_id(message: Mapping[str, Any]) -> int:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            raise ValueError("Telegram update has no chat id")
        return chat_id

    @staticmethod
    def _status(task: Task) -> str:
        return str(task.status).lower()

    @classmethod
    def _format_tasks(cls, title: str, tasks: list[Task]) -> str:
        if not tasks:
            return f"{title}\n\nЗадач нет."
        lines = [title, ""]
        for index, task in enumerate(tasks, 1):
            due = f" — до {task.due_at.isoformat()}" if task.due_at else ""
            lines.append(f"{index}. {task.title} [{cls._status(task)}]{due}")
            lines.append(f"   id: {task.id}")
        return "\n".join(lines)

    @staticmethod
    def _keyboard(tasks: list[Task]) -> dict[str, Any] | None:
        if not tasks:
            return None
        task = tasks[0]
        prefix = f"task:{{action}}:{task.id}:{task.version}"
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ Выполнено", "callback_data": prefix.format(action="done")},
                    {"text": "✏️ Изменить", "callback_data": prefix.format(action="edit")},
                ],
                [
                    {"text": "↔ Перенести", "callback_data": prefix.format(action="postpone")},
                    {"text": "⏳ Жду", "callback_data": prefix.format(action="waiting")},
                ],
                [
                    {"text": "❌ Отмена", "callback_data": prefix.format(action="cancel")},
                    {"text": "🔔 Напомнить", "callback_data": prefix.format(action="remind")},
                ],
            ]
        }

    @staticmethod
    def _help_text() -> str:
        return (
            "Команды:\n"
            "/new текст — создать задачу\n"
            "/today — задачи на сегодня\n"
            "/week — задачи на неделю\n"
            "/overdue — просроченные\n"
            "/delegated — делегированные\n"
            "/waiting — ожидаемые\n"
            "/search текст — поиск\n"
            "/settings — настройки\n"
            "/edit ID новый текст — изменить задачу"
        )
