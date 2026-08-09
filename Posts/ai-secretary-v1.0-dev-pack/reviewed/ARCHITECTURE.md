# Architecture — AI Secretary v1.0

## 1. Контекст

AI Secretary — Telegram-first приложение для управления обязательствами. OpenClaw может предоставлять LLM и агентный интерфейс, но Task Core должен работать независимо от конкретной модели.

## 2. Высокоуровневая схема

```text
Telegram ───────┐
                │
Gmail ──────────┼────► Inbox Service ─► LLM Parser ─► Policy ─► Task Service
                │                                             │
Calendar ◄──────┘                                             ▼
                                                        PostgreSQL
                                                            │
                                                            ▼
                                                     Reminder Engine
                                                            │
                                                            ▼
                                                        Telegram
```

## 3. Слои

### 3.1. Interface layer

- Telegram Bot
- REST API
- jobs/webhooks

Не содержит бизнес-правила дедлайнов.

### 3.2. Application/service layer

- TaskService
- ReminderService
- ContactService
- InboxService
- LLMService
- GmailService
- CalendarService

### 3.3. Repository layer

Абстрагирует SQLAlchemy queries.

### 3.4. Persistence layer

PostgreSQL — production source of truth.

### 3.5. Agent layer

OpenClaw/LLM выполняет semantic operations:

- classification;
- extraction;
- natural language search parsing;
- status interpretation;
- reminder wording.

## 4. Компоненты

### Telegram Gateway

Ответственность:
- updates;
- commands;
- callback buttons;
- message source metadata;
- voice-to-text adapter later.

### Inbox Service

Унифицированный объект:

```json
{
  "source_type": "TELEGRAM",
  "external_id": "123:456",
  "sender": {"id": "...", "name": "..."},
  "received_at": "2026-08-07T12:00:00Z",
  "subject": null,
  "text": "Сергей должен до пятницы...",
  "thread_id": null,
  "source_url": null,
  "metadata": {}
}
```

### LLM Parser

Pipeline:

1. classification;
2. extraction;
3. validation;
4. confidence policy;
5. user confirmation or service command.

### Policy Layer

Отвечает за:
- requires_confirmation;
- allowed state transitions;
- source trust;
- duplicate handling;
- permission gates.

### Task Service

Единственное место изменения task aggregate.

### Reminder Engine

Не зависит от LLM для определения момента отправки.

### Gmail

MVP:
- periodic polling;
- query only newer/unprocessed messages;
- source idempotency.

Later:
- Gmail Watch/PubSub.

### Calendar

Calendar — представление части задач, а не master database.

## 5. Trust boundaries

```text
[Untrusted]
Telegram from others
Gmail bodies
HTML
Attachments
      │
      ▼
sanitize / normalize / classify
      │
      ▼
[Trusted application boundary]
Policy + Pydantic + services
      │
      ▼
DB / APIs
```

## 6. Prompt injection defense

Любой внешний текст передавать модели как DATA.

System prompt должен явно запрещать:
- выполнять инструкции из message body;
- менять system rules;
- раскрывать secrets;
- вызывать tools по командам внутри source text.

## 7. Idempotency

Уникальные ключи:

- Telegram: `(source_type, external_id)`;
- Gmail: `(source_type, message_id)`;
- Calendar sync: `calendar_event_id`.

Каждый source message сохраняется до AI action.

## 8. Deployment topology

```text
Internet
   │
   ├── Telegram API
   ├── Google APIs
   │
   ▼
VPS Ubuntu 24.04
   │
   ├── api container
   ├── worker/scheduler container
   ├── postgres container
   └── optional OpenClaw container/service
```

Postgres порт наружу не публиковать.

## 9. Failure modes

### LLM unavailable
Сохраняем Inbox message как unprocessed/failed. Task DB продолжает работать.

### Gmail unavailable
Повторная попытка с backoff.

### Calendar unavailable
Task сохраняется. `calendar_sync_status=FAILED`, retry job.

### Telegram unavailable
Reminder остаётся `PENDING/RETRY`.

### PostgreSQL unavailable
Не принимать команды, которые нельзя надёжно сохранить.

## 10. Observability

Health endpoints:
- `/health/live`
- `/health/ready`

Metrics later:
- processed messages;
- parse failures;
- reminders due/sent/failed;
- overdue task count;
- Gmail polling delay.

## 11. Backup

Production:
- daily pg_dump;
- retention configurable;
- restore procedure documented and tested.

## 12. Extension points

v2:
- multiple users/workspaces;
- WhatsApp/VK;
- voice;
- web dashboard;
- project hierarchy;
- automation policies per contact;
- vector search for contextual retrieval.
