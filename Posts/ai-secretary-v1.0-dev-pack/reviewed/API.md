# REST API — AI Secretary v1.0

Base URL:

```text
/api/v1
```

Все ответы JSON.

## 1. Error format

```json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Task not found",
    "details": {},
    "request_id": "..."
  }
}
```

## 2. Authentication

MVP:
- internal bearer token или owner auth;
- Telegram handler вызывает service layer напрямую либо через internal API.

Production API не должен быть публичным без auth.

---

# Tasks

## POST /tasks

Создать задачу.

Request:

```json
{
  "title": "Получить договор",
  "description": "Финальная версия",
  "task_type": "AWAITING",
  "priority": "P2",
  "assignee_contact_id": "uuid",
  "due_at": "2026-08-10T15:00:00Z",
  "due_precision": "EXACT",
  "source_message_id": "uuid"
}
```

Response `201`:

```json
{
  "id": "uuid",
  "status": "NEW",
  "title": "Получить договор"
}
```

## GET /tasks/{task_id}

Получить карточку.

## GET /tasks

Query:
- `status`
- `task_type`
- `priority`
- `assignee_contact_id`
- `due_from`
- `due_to`
- `q`
- `limit`
- `cursor`

## PATCH /tasks/{task_id}

Partial update.

Требует optimistic version:

```json
{
  "version": 3,
  "title": "Новый заголовок",
  "priority": "P1"
}
```

Conflict → `409`.

## POST /tasks/{task_id}/complete

```json
{
  "version": 3,
  "comment": "Получено в Telegram"
}
```

## POST /tasks/{task_id}/postpone

```json
{
  "version": 3,
  "new_due_at": "2026-08-12T12:00:00Z",
  "reason": "Исполнитель попросил перенос"
}
```

## POST /tasks/{task_id}/status

```json
{
  "version": 3,
  "status": "WAITING",
  "comment": "Ждём ответ банка"
}
```

## POST /tasks/{task_id}/cancel

Не физическое удаление.

---

# Task Views

## GET /tasks/views/today

Задачи пользователя на локальный сегодня.

## GET /tasks/views/week

## GET /tasks/views/overdue

## GET /tasks/views/delegated

## GET /tasks/views/waiting

---

# Natural language

## POST /messages/analyze

Request:

```json
{
  "source_type": "TELEGRAM",
  "external_id": "123:456",
  "sender": {
    "external_id": "123",
    "name": "User"
  },
  "text": "Сергей должен до пятницы прислать смету",
  "received_at": "2026-08-07T12:00:00Z"
}
```

Response:

```json
{
  "classification": "DELEGATION",
  "confidence": 0.96,
  "candidate": {
    "task_type": "DELEGATED",
    "title": "Получить смету от Сергея",
    "assignee": "Сергей",
    "due_at": "2026-08-14T20:59:59Z",
    "due_precision": "DATE",
    "priority": "P3"
  },
  "requires_confirmation": true
}
```

## POST /messages/{source_message_id}/confirm-task

Создать задачу из candidate после подтверждения.

## POST /messages/{source_message_id}/ignore

---

# Search

## POST /search/parse

Natural language → structured filter.

Request:

```json
{
  "query": "Что мне должен Иван на этой неделе?"
}
```

Response:

```json
{
  "filters": {
    "task_type": ["DELEGATED", "AWAITING"],
    "assignee_name": "Иван",
    "due_range": "THIS_WEEK",
    "exclude_status": ["DONE", "CANCELLED"]
  }
}
```

## POST /search/tasks

Можно передавать structured filters напрямую.

---

# Contacts

## GET /contacts

## POST /contacts

```json
{
  "name": "Сергей",
  "email": "sergey@example.com",
  "relation_type": "COLLEAGUE",
  "trust_level": "KNOWN"
}
```

## PATCH /contacts/{id}

---

# Reminders

## GET /reminders/due

Internal endpoint.

## POST /reminders/{id}/sent

Internal.

## POST /tasks/{id}/snooze

```json
{
  "until": "2026-08-08T10:00:00Z"
}
```

---

# Gmail

## POST /integrations/gmail/poll

Internal/admin endpoint.

Response:

```json
{
  "new_messages": 12,
  "candidates": 2,
  "ignored": 8,
  "failed": 0
}
```

## POST /integrations/gmail/process/{message_id}

## GET /integrations/gmail/status

Later:
- `/gmail/watch/start`
- `/gmail/watch/renew`
- `/gmail/webhook`

---

# Calendar

## POST /tasks/{task_id}/calendar

Создать event после user confirmation.

```json
{
  "mode": "DEADLINE",
  "start_at": "2026-08-10T14:30:00Z",
  "end_at": "2026-08-10T15:00:00Z"
}
```

## PATCH /tasks/{task_id}/calendar

## DELETE /tasks/{task_id}/calendar

Требует явного user confirmation.

---

# Settings

## GET /settings

## PATCH /settings

```json
{
  "morning_digest_time": "08:30",
  "quiet_hours_start": "22:30",
  "quiet_hours_end": "07:30",
  "gmail_poll_minutes": 15
}
```

---

# Health

## GET /health/live

Process alive.

## GET /health/ready

Проверяет:
- DB;
- migrations/schema;
- critical config.

Не блокировать ready из-за временно недоступной LLM, если выбран degraded mode.

---

# Status codes

- 200 OK
- 201 Created
- 202 Accepted
- 400 Invalid request
- 401 Unauthorized
- 403 Forbidden
- 404 Not found
- 409 Version conflict / duplicate
- 422 Validation error
- 429 Rate limit
- 500 Internal error
- 503 Dependency unavailable
