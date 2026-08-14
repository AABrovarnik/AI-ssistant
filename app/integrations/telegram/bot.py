"""Telegram commands and callbacks for Phase 2.

The bot talks to the TaskService directly. This keeps Telegram independent from
the REST transport and makes all handlers testable without Telegram or an LLM.
"""

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.telegram.client import (
    TelegramAuthenticationError,
    TelegramClientProtocol,
)
from app.jobs.reminders import (
    create_follow_up_reminder,
    create_manual_reminder,
    snooze_task_reminders,
)
from app.llm.provider import LLMProviderError
from app.llm.schemas import (
    MessageClassification,
    SearchDateFilter,
    SearchFilters,
    TaskCandidate,
)
from app.llm.service import LLMParseError, LLMService
from app.tasks.models import (
    DuePrecision,
    ProcessingStatus,
    Task,
    TaskEvent,
    TaskPriority,
    TaskStatus,
    TaskType,
)
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
        timezone: str = "UTC",
    ) -> None:
        self.client = client
        self.session_factory = session_factory
        self.owner_user_id = owner_user_id
        self.llm_service = llm_service
        self.timezone = timezone

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
                    chat_id,
                    self._format_tasks(title, tasks, self.timezone),
                    self._keyboard(tasks),
                )
        elif command == "/search":
            async with self.session_factory() as session:
                tasks = await TaskService(session).list(query=argument.strip())
            await self.client.send_message(
                chat_id,
                self._format_tasks("Результаты поиска", tasks, self.timezone),
                self._keyboard(tasks),
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
        message_id = str(message.get("message_id", "unknown"))
        edited_task: Task | None = None
        edit_error: str | None = None
        async with self.session_factory() as session:
            service = TaskService(session)
            pending_task = await service.get_pending_edit_task()
            if pending_task is not None:
                field = str(pending_task.extra.get("awaiting_edit_field", "title"))
                update: TaskUpdate | None
                status = None
                if field == "due":
                    update = self._parse_due_update(text)
                    if update is None:
                        edit_error = (
                            "Не распознал срок. Пришли дату как 12.08.2026 18:00 "
                            "или 12.08.2026."
                        )
                elif field == "status":
                    status = self._parse_status(text)
                    update = (
                        TaskUpdate(version=pending_task.version, status=status)
                        if status
                        else None
                    )
                    if update is None:
                        edit_error = (
                            "Не распознал статус. Используй: новая, в работе, жду, "
                            "выполнено, отменено или исполнитель/отправитель не известен."
                        )
                elif field == "assignee":
                    if not text.strip():
                        edit_error = "Пришли имя исполнителя или отправителя."
                        update = None
                    else:
                        contact = await service.get_or_create_contact(
                            text, pending_task.user_id
                        )
                        update = TaskUpdate(
                            version=pending_task.version,
                            assignee_contact_id=contact.id,
                        )
                        pending_task.extra = {
                            **pending_task.extra,
                            "assignee_name": contact.name,
                        }
                        if pending_task.status == TaskStatus.UNKNOWN_PARTY:
                            update.status = TaskStatus.NEW
                else:
                    update = TaskUpdate(version=pending_task.version, title=text)

                if update is not None:
                    operation_key = (
                        f"telegram:edit-pending:{pending_task.id}:"
                        f"{pending_task.version}:{message_id}"
                    )
                    if status == TaskStatus.CANCELLED:
                        edited_task = await service.cancel(
                            pending_task.id,
                            pending_task.version,
                            operation_key,
                        )
                    else:
                        edited_task = await service.update(
                            pending_task.id,
                            update,
                            operation_key,
                        )
                    edited_task.extra = {
                        key: value
                        for key, value in edited_task.extra.items()
                        if key not in {"awaiting_edit", "awaiting_edit_field"}
                    }
                    await session.commit()
        if edit_error is not None:
            await self.client.send_message(chat_id, edit_error)
            return
        if edited_task is not None:
            await self.client.send_message(
                chat_id,
                self._format_tasks("Задача изменена", [edited_task], self.timezone),
                self._keyboard([edited_task]),
            )
            return
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
                source = await service.get_pending_candidate()
                if source is None:
                    source = await service.create_source_message(
                        SourceMessageCreate(
                            source_type="TELEGRAM",
                            external_id=f"{self.owner_user_id}:{message_id}",
                            sender_external_id=str(self.owner_user_id),
                            sender_name=sender_name,
                            text=text,
                        )
                    )
                else:
                    source.text = text
                    source.sender_name = sender_name
                    await session.flush()
                source = await service.save_source_candidate(
                    source.id,
                    classification.classification.value,
                    classification.confidence,
                    candidate.model_dump(mode="json"),
                )
            await self.client.send_message(
                chat_id,
                self._format_candidate(candidate, self.timezone),
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
            self._format_tasks("Результаты поиска", tasks, self.timezone),
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
    def _format_candidate(candidate: TaskCandidate, timezone: str = "UTC") -> str:
        lines = [
            "Кандидат задачи",
            f"Тип: {candidate.task_type.value}",
            f"Заголовок: {candidate.title}",
            f"Приоритет: {candidate.priority.value}",
            f"Уверенность: {candidate.confidence:.0%}",
        ]
        if TelegramBot._candidate_status(candidate) == TaskStatus.UNKNOWN_PARTY:
            lines.append("Статус: Исполнитель/отправитель не известен")
        if candidate.assignee_name:
            lines.append(f"Исполнитель: {candidate.assignee_name}")
        if candidate.due_at:
            lines.append(f"Срок: {TelegramBot._format_datetime(candidate.due_at, timezone)}")
        elif candidate.due_date:
            lines.append(f"Срок: {TelegramBot._format_date(candidate.due_date)}")
        lines.append("\nЗадача пока не создана — выбери действие ниже.")
        return "\n".join(lines)

    @staticmethod
    def _candidate_status(candidate: TaskCandidate) -> TaskStatus:
        if candidate.task_type in {TaskType.DELEGATED, TaskType.AWAITING} and not (
            candidate.assignee_name and candidate.assignee_name.strip()
        ):
            return TaskStatus.UNKNOWN_PARTY
        return TaskStatus.NEW

    @staticmethod
    def _zone_info(timezone: str) -> ZoneInfo:
        try:
            return ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    @staticmethod
    def _format_datetime(value: datetime, timezone: str = "UTC") -> str:
        return value.astimezone(TelegramBot._zone_info(timezone)).strftime("%d.%m.%Y %H:%M")

    @staticmethod
    def _format_date(value: date) -> str:
        return value.strftime("%d.%m.%Y")

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
            self._format_tasks("Задача создана", [task], self.timezone),
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
        if len(parts) == 2 and parts[0] == "digest":
            await self._handle_digest_callback(callback, parts[1])
            return
        if len(parts) == 3 and parts[0] == "candidate":
            await self._handle_candidate_callback(callback, parts[1], parts[2])
            return
        if len(parts) == 4 and parts[0] == "followup":
            await self._handle_followup_callback(callback, parts[1], parts[2], parts[3])
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
                    await create_manual_reminder(
                        session, task, datetime.now(UTC) + timedelta(hours=1)
                    )
                    notice = "Напоминание создано на час"
                elif action == "snooze":
                    task = await service.get(task_id)
                    until = datetime.now(UTC) + timedelta(hours=1)
                    count = await snooze_task_reminders(session, task.id, until)
                    notice = (
                        "Напоминания отложены на час"
                        if count
                        else "Активных напоминаний для этой задачи нет"
                    )
                elif action == "edit":
                    task = await service.get(task_id)
                    task.extra = {
                        **task.extra,
                        "awaiting_edit": True,
                        "awaiting_edit_field": "title",
                    }
                    await session.commit()
                    await self.client.answer_callback_query(
                        callback_id, "Пришли новый текст задачи"
                    )
                    message_id = message.get("message_id")
                    if isinstance(message_id, int):
                        await self.client.edit_message_text(
                            chat_id,
                            message_id,
                            "Пришли новый текст задачи отдельным сообщением.",
                        )
                    return
                elif action in {"due", "status"}:
                    task = await service.get(task_id)
                    task.extra = {
                        **task.extra,
                        "awaiting_edit": True,
                        "awaiting_edit_field": action,
                    }
                    await session.commit()
                    prompt = (
                        "Пришли новый срок: 12.08.2026 18:00 или 12.08.2026."
                        if action == "due"
                        else "Пришли статус: новая, в работе, жду, выполнено или отменено."
                    )
                    await self.client.answer_callback_query(callback_id, prompt)
                    message_id = message.get("message_id")
                    if isinstance(message_id, int):
                        await self.client.edit_message_text(chat_id, message_id, prompt)
                    return
                elif action == "assignee":
                    task = await service.get(task_id)
                    task.extra = {
                        **task.extra,
                        "awaiting_edit": True,
                        "awaiting_edit_field": "assignee",
                    }
                    await session.commit()
                    prompt = "Пришли имя исполнителя или отправителя отдельным сообщением."
                    await self.client.answer_callback_query(callback_id, prompt)
                    message_id = message.get("message_id")
                    if isinstance(message_id, int):
                        await self.client.edit_message_text(chat_id, message_id, prompt)
                    return
                elif action == "history":
                    task = await service.get(task_id)
                    events = await service.get_task_history(task_id)
                    history_text = self._format_history(task, events, self.timezone)
                    await self.client.answer_callback_query(callback_id, "История изменений")
                    await self.client.send_message(chat_id, history_text)
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
                self._format_tasks("Задача обновлена", [task], self.timezone),
                self._keyboard([task]),
            )

    async def _handle_followup_callback(
        self,
        callback: Mapping[str, Any],
        action: str,
        task_id_text: str,
        version_text: str,
    ) -> None:
        callback_id = str(callback.get("id", ""))
        message = callback.get("message") or {}
        chat_id = self._chat_id(message)
        try:
            task_id = UUID(task_id_text)
            version = int(version_text)
        except ValueError:
            await self.client.answer_callback_query(callback_id, "Некорректная задача")
            return
        operation_key = f"telegram:followup:{callback_id}"
        try:
            async with self.session_factory() as session:
                service = TaskService(session)
                task = await service.get(task_id)
                if TaskType(task.task_type) != TaskType.AWAITING:
                    raise ValueError("задача больше не находится в ожидании")
                if action == "done":
                    task = await service.complete(task_id, operation_key, version)
                    notice = "Результат отмечен полученным"
                    text = self._format_tasks("Ожидание закрыто", [task], self.timezone)
                elif action == "snooze":
                    await create_follow_up_reminder(
                        session,
                        task,
                        datetime.now(UTC) + timedelta(days=1),
                        operation_key,
                    )
                    notice = "Напомню завтра"
                    text = "⏰ Follow-up перенесён на завтра."
                else:
                    raise ValueError("неизвестное действие follow-up")
        except (
            TaskNotFoundError,
            VersionConflictError,
            InvalidTaskTransitionError,
            ValueError,
        ) as exc:
            await self.client.answer_callback_query(callback_id, f"Не выполнено: {exc}")
            return
        await self.client.answer_callback_query(callback_id, notice)
        message_id = message.get("message_id")
        if isinstance(message_id, int):
            await self.client.edit_message_text(chat_id, message_id, text)

    async def _handle_digest_callback(
        self, callback: Mapping[str, Any], action: str
    ) -> None:
        callback_id = str(callback.get("id", ""))
        message = callback.get("message") or {}
        chat_id = self._chat_id(message)
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            service = TaskService(session)
            if action == "overdue":
                tasks = await service.list_overdue()
                title = "Просроченные задачи"
            elif action == "delegated":
                tasks = await service.list(task_type=TaskType.DELEGATED)
                title = "Делегированные задачи"
            elif action == "waiting":
                tasks = await service.list(task_type=TaskType.AWAITING)
                title = "Ожидаемые результаты"
            elif action == "today":
                local_now = now.astimezone(self._zone_info(self.timezone))
                start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                tasks = await service.list(
                    due_from=start.astimezone(UTC),
                    due_to=(start + timedelta(days=1)).astimezone(UTC),
                )
                title = "Задачи на сегодня"
            else:
                await self.client.answer_callback_query(callback_id, "Неизвестный список")
                return
        await self.client.answer_callback_query(callback_id, title)
        await self.client.send_message(
            chat_id,
            self._format_tasks(title, tasks, self.timezone),
            self._keyboard(tasks),
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
                    source.extra = {**source.extra, "awaiting_edit": True}
                    await session.commit()
                    await self.client.answer_callback_query(
                        callback_id,
                        "Отправь исправленный вариант отдельным сообщением",
                    )
                    message_id = message.get("message_id")
                    if isinstance(message_id, int):
                        await self.client.edit_message_text(
                            chat_id,
                            message_id,
                            "Пришли исправленный вариант задачи отдельным сообщением.",
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
                            status=self._candidate_status(candidate),
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
                self._format_tasks("Задача создана", [task], self.timezone),
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
    def _format_tasks(cls, title: str, tasks: list[Task], timezone: str = "UTC") -> str:
        if not tasks:
            return f"{title}\n\nЗадач нет."
        lines = [title, ""]
        for index, task in enumerate(tasks, 1):
            lines.append(f"{index}. {task.title}")
            lines.append(f"   Статус: {cls._status_label(task)}")
            if task.due_at:
                lines.append(f"   Срок: {cls._format_datetime(task.due_at, timezone)}")
            elif task.due_date:
                lines.append(f"   Срок: {cls._format_date(task.due_date)}")
            else:
                lines.append("   Срок: не указан")
            lines.append(f"   Приоритет: {cls._priority_label(task)}")
            lines.append(f"   Тип: {cls._type_label(task)}")
            assignee_name = (task.extra or {}).get("assignee_name")
            if isinstance(assignee_name, str) and assignee_name:
                lines.append(f"   Исполнитель: {assignee_name}")
            if task.description:
                lines.append(f"   Описание: {task.description}")
        return "\n".join(lines)

    @staticmethod
    def _status_label(task: Task) -> str:
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
        try:
            status = TaskStatus(task.status)
        except (TypeError, ValueError):
            return str(task.status).lower()
        return labels.get(status, str(task.status).lower())

    @staticmethod
    def _priority_label(task: Task) -> str:
        labels = {
            TaskPriority.P1: "P1 — критичный",
            TaskPriority.P2: "P2 — высокий",
            TaskPriority.P3: "P3 — обычный",
            TaskPriority.P4: "P4 — низкий",
        }
        try:
            priority = TaskPriority(task.priority)
        except (TypeError, ValueError):
            priority = TaskPriority.P3
        return labels.get(priority, str(task.priority))

    @staticmethod
    def _type_label(task: Task) -> str:
        labels = {
            TaskType.MY_TASK: "Моя задача",
            TaskType.DELEGATED: "Делегированная",
            TaskType.AWAITING: "Ожидаемая",
        }
        try:
            task_type = TaskType(task.task_type)
        except (TypeError, ValueError):
            task_type = TaskType.MY_TASK
        return labels.get(task_type, str(task.task_type))

    @staticmethod
    def _status_value_label(value: object) -> str:
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
        try:
            return labels.get(TaskStatus(str(value)), str(value))
        except ValueError:
            return str(value)

    @classmethod
    def _format_history(
        cls, task: Task, events: Sequence[TaskEvent], timezone: str = "UTC"
    ) -> str:
        if not events:
            return f"История изменений: {task.title}\n\nИзменений пока нет."
        field_labels = {
            "title": "Название",
            "description": "Описание",
            "due_at": "Срок",
            "due_date": "Дата срока",
            "due_precision": "Точность срока",
            "priority": "Приоритет",
            "task_type": "Тип",
            "status": "Статус",
        }
        lines = [f"История изменений: {task.title}", ""]
        for event in events:
            timestamp = cls._format_datetime(event.created_at, timezone)
            if event.event_type == "TASK_CREATED":
                description = "Создана"
            elif event.event_type == "TASK_CANCELLED":
                description = "Статус: Отменена"
            elif event.event_type == "STATUS_CHANGED":
                old_status = (event.old_value or {}).get("status", "—")
                new_status = (event.new_value or {}).get("status", "—")
                description = (
                    f"Статус: {cls._status_value_label(old_status)} → "
                    f"{cls._status_value_label(new_status)}"
                )
            else:
                changes: list[str] = []
                old_values = event.old_value or {}
                new_values = event.new_value or {}
                for key, new_value in new_values.items():
                    label = field_labels.get(key, key)
                    old_value = old_values.get(key, "—")
                    changes.append(
                        f"{label}: {cls._format_history_value(key, old_value, timezone)} → "
                        f"{cls._format_history_value(key, new_value, timezone)}"
                    )
                description = "; ".join(changes) or event.event_type
            lines.append(f"{timestamp} — {description}")
        return "\n".join(lines)

    @classmethod
    def _format_history_value(cls, field: str, value: object, timezone: str) -> str:
        if value is None or value == "" or value == "—":
            return "—"
        if field == "status":
            return cls._status_value_label(value)
        if field == "due_at":
            try:
                return cls._format_datetime(datetime.fromisoformat(str(value)), timezone)
            except ValueError:
                return str(value)
        return str(value)

    def _parse_due_update(self, text: str) -> TaskUpdate | None:
        value = text.strip().lower()
        zone = self._zone_info(self.timezone)
        now = datetime.now(zone)
        relative_match = re.fullmatch(
            r"(сегодня|завтра|послезавтра)(?:\s+(\d{1,2}):(\d{2}))?", value
        )
        if relative_match:
            offsets = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
            local_date = (now + timedelta(days=offsets[relative_match.group(1)])).date()
            hour = int(relative_match.group(2) or 9)
            minute = int(relative_match.group(3) or 0)
            try:
                local_datetime = datetime.combine(
                    local_date,
                    time(hour, minute),
                    tzinfo=zone,
                )
            except ValueError:
                return None
            return TaskUpdate(
                due_at=local_datetime.astimezone(UTC),
                due_date=None,
                due_precision=DuePrecision.EXACT,
            )

        match = re.fullmatch(
            r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?:\s+(\d{1,2}):(\d{2}))?",
            value,
        )
        if not match:
            return None
        try:
            local_date = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            if match.group(4) is None:
                return TaskUpdate(
                    due_at=None,
                    due_date=local_date,
                    due_precision=DuePrecision.DATE,
                )
            local_datetime = datetime.combine(
                local_date,
                time(int(match.group(4)), int(match.group(5))),
                tzinfo=zone,
            )
        except ValueError:
            return None
        return TaskUpdate(
            due_at=local_datetime.astimezone(UTC),
            due_date=None,
            due_precision=DuePrecision.EXACT,
        )

    @staticmethod
    def _parse_status(text: str) -> TaskStatus | None:
        normalized = " ".join(text.lower().replace("ё", "е").split())
        statuses = {
            "новая": TaskStatus.NEW,
            "новый": TaskStatus.NEW,
            "new": TaskStatus.NEW,
            "исполнитель/отправитель не известен": TaskStatus.UNKNOWN_PARTY,
            "исполнитель не известен": TaskStatus.UNKNOWN_PARTY,
            "отправитель не известен": TaskStatus.UNKNOWN_PARTY,
            "unknown party": TaskStatus.UNKNOWN_PARTY,
            "запланирована": TaskStatus.PLANNED,
            "запланировано": TaskStatus.PLANNED,
            "planned": TaskStatus.PLANNED,
            "в работе": TaskStatus.IN_PROGRESS,
            "работа": TaskStatus.IN_PROGRESS,
            "in progress": TaskStatus.IN_PROGRESS,
            "жду": TaskStatus.WAITING,
            "ожидание": TaskStatus.WAITING,
            "waiting": TaskStatus.WAITING,
            "выполнено": TaskStatus.DONE,
            "выполнена": TaskStatus.DONE,
            "готово": TaskStatus.DONE,
            "done": TaskStatus.DONE,
            "отменено": TaskStatus.CANCELLED,
            "отмена": TaskStatus.CANCELLED,
            "cancelled": TaskStatus.CANCELLED,
        }
        return statuses.get(normalized)

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
                    {"text": "✏️ Название", "callback_data": prefix.format(action="edit")},
                ],
                [
                    {"text": "📅 Срок", "callback_data": prefix.format(action="due")},
                    {"text": "🔄 Статус", "callback_data": prefix.format(action="status")},
                ],
                [
                    {
                        "text": "👤 Исполнитель",
                        "callback_data": prefix.format(action="assignee"),
                    }
                ],
                [
                    {"text": "↔ Перенести", "callback_data": prefix.format(action="postpone")},
                    {"text": "⏳ Жду", "callback_data": prefix.format(action="waiting")},
                ],
                [
                    {"text": "❌ Отмена", "callback_data": prefix.format(action="cancel")},
                    {"text": "🔔 Напомнить", "callback_data": prefix.format(action="remind")},
                ],
                [{"text": "😴 Отложить", "callback_data": prefix.format(action="snooze")}],
                [{"text": "📜 История", "callback_data": prefix.format(action="history")}],
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
