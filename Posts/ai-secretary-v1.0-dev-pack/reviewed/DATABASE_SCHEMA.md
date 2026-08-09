# Database Schema — AI Secretary v1.0

## Общие правила

- PostgreSQL UUID primary keys.
- Все timestamps timezone-aware.
- UTC в БД.
- Soft delete предпочтительнее физического удаления для задач.
- Существенные изменения → `task_events`.
- JSONB — только для расширяемых metadata, не вместо нормальных полей.
- **ORM naming caveat.** Колонка `metadata` во всех таблицах ниже — это имя SQL-колонки, а не имя Python-атрибута. В SQLAlchemy 2.x `Base.metadata` зарезервировано под объект `MetaData`, поэтому модель должна маппить эту колонку на другое имя атрибута, например:
  ```python
  extra: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
  ```
  Буквальное `metadata: Mapped[dict] = mapped_column(...)` в декларативной модели упадёт с `InvalidRequestError` при старте приложения.

---

## 1. users

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    telegram_user_id BIGINT UNIQUE,
    name TEXT NOT NULL,
    email TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    language TEXT NOT NULL DEFAULT 'ru',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`timezone` по умолчанию `UTC` — это fallback на случай, если onboarding ещё не завершён, а не рекомендуемое значение. При первом запуске (`/start` в Telegram) сервис должен запросить у владельца его реальный IANA timezone (например `Europe/Amsterdam`) и сохранить его, а не полагаться на DEFAULT.

---

## 2. contacts

```sql
CREATE TABLE contacts (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    telegram_username TEXT,
    telegram_chat_id BIGINT,
    email TEXT,
    relation_type TEXT NOT NULL DEFAULT 'OTHER',
    trust_level TEXT NOT NULL DEFAULT 'KNOWN',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Relation:
- COLLEAGUE
- FAMILY
- FRIEND
- CLIENT
- CONTRACTOR
- OTHER

Trust:
- TRUSTED
- KNOWN
- UNKNOWN
- AUTOMATED

---

## 3. tasks

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    title TEXT NOT NULL,
    description TEXT,

    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW',
    priority TEXT NOT NULL DEFAULT 'P3',

    assignee_contact_id UUID REFERENCES contacts(id),
    created_by_contact_id UUID REFERENCES contacts(id),

    start_at TIMESTAMPTZ,
    due_at TIMESTAMPTZ,
    due_date DATE,
    due_precision TEXT NOT NULL DEFAULT 'UNKNOWN',

    next_check_at TIMESTAMPTZ,
    last_reminded_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    source_type TEXT,
    source_id TEXT,
    source_url TEXT,

    calendar_event_id TEXT,
    calendar_sync_status TEXT,

    confidence NUMERIC(4,3),

    parent_task_id UUID REFERENCES tasks(id),

    is_recurring BOOLEAN NOT NULL DEFAULT FALSE,
    recurrence_rule TEXT,

    version INTEGER NOT NULL DEFAULT 1,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
```

### task_type

- MY_TASK
- DELEGATED
- AWAITING

> Переименовано из `WAITING` в `AWAITING`, чтобы не совпадать по имени со значением `status`. Смысл не изменился: это тип задачи "я жду результата от кого-то/чего-то". `status.WAITING` — про рабочее состояние самой задачи (в т.ч. любой из трёх типов может быть приостановлен) и означает другое.

### status

- NEW
- PLANNED
- IN_PROGRESS
- WAITING
- DONE
- OVERDUE
- POSTPONED
- ON_HOLD
- CANCELLED

### priority

- P1
- P2
- P3
- P4

### due_precision

- EXACT
- DATE
- APPROXIMATE
- UNKNOWN

### indexes

```sql
CREATE INDEX idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX idx_tasks_user_due ON tasks(user_id, due_at);
CREATE INDEX idx_tasks_assignee ON tasks(assignee_contact_id);
CREATE INDEX idx_tasks_next_check ON tasks(next_check_at);
```

---

## 4. source_messages

```sql
CREATE TABLE source_messages (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    source_type TEXT NOT NULL,
    external_id TEXT NOT NULL,

    sender_external_id TEXT,
    sender_name TEXT,
    sender_email TEXT,

    subject TEXT,
    text TEXT,
    received_at TIMESTAMPTZ,

    thread_id TEXT,
    source_url TEXT,

    processing_status TEXT NOT NULL DEFAULT 'NEW',
    classification TEXT,
    confidence NUMERIC(4,3),
    error_code TEXT,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,

    UNIQUE(user_id, source_type, external_id)
);
```

processing_status:
- NEW
- PROCESSING
- PROCESSED
- IGNORED
- FAILED

---

## 5. task_sources

```sql
CREATE TABLE task_sources (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    source_message_id UUID NOT NULL REFERENCES source_messages(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(task_id, source_message_id, relation)
);
```

relation:
- CREATED_FROM
- STATUS_UPDATE
- CONFIRMATION
- DISCUSSION
- ATTACHMENT

---

## 6. task_events

```sql
CREATE TABLE task_events (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT,

    old_value JSONB,
    new_value JSONB,

    source_message_id UUID REFERENCES source_messages(id),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

event_type examples:
- TASK_CREATED
- TASK_UPDATED
- STATUS_CHANGED
- DEADLINE_CHANGED
- ASSIGNEE_CHANGED
- PRIORITY_CHANGED
- REMINDER_CREATED
- REMINDER_SENT
- TASK_COMPLETED
- TASK_CANCELLED
- COMMENT_ADDED
- CALENDAR_LINKED

actor_type:
- USER
- SYSTEM
- AI
- EXTERNAL_CONTACT

---

## 7. reminders

```sql
CREATE TABLE reminders (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    remind_at TIMESTAMPTZ NOT NULL,
    reminder_type TEXT NOT NULL,
    recipient_type TEXT NOT NULL DEFAULT 'OWNER',

    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,

    sent_at TIMESTAMPTZ,
    last_error TEXT,

    dedupe_key TEXT UNIQUE,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

reminder_type:
- PRE_DEADLINE
- DEADLINE
- OVERDUE
- STATUS_CHECK
- FOLLOW_UP
- DAILY_DIGEST
- EVENING_REVIEW
- WEEKLY_REVIEW

status:
- PENDING
- SENT
- RETRY
- CANCELLED
- FAILED

---

## 8. user_settings

```sql
CREATE TABLE user_settings (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    morning_digest_time TIME NOT NULL DEFAULT '08:00',
    evening_digest_time TIME NOT NULL DEFAULT '19:00',
    weekly_review_day INTEGER NOT NULL DEFAULT 1,

    quiet_hours_start TIME NOT NULL DEFAULT '22:00',
    quiet_hours_end TIME NOT NULL DEFAULT '07:00',

    auto_create_owner_tasks BOOLEAN NOT NULL DEFAULT FALSE,
    auto_create_external_tasks BOOLEAN NOT NULL DEFAULT FALSE,
    auto_calendar BOOLEAN NOT NULL DEFAULT FALSE,

    gmail_poll_minutes INTEGER NOT NULL DEFAULT 15,

    default_priority TEXT NOT NULL DEFAULT 'P3',

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 9. integration_accounts

```sql
CREATE TABLE integration_accounts (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    provider TEXT NOT NULL,
    external_account_id TEXT,

    encrypted_access_token TEXT,
    encrypted_refresh_token TEXT,
    token_expires_at TIMESTAMPTZ,

    scopes TEXT[],
    status TEXT NOT NULL DEFAULT 'ACTIVE',

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(user_id, provider, external_account_id)
);
```

Provider:
- GMAIL
- GOOGLE_CALENDAR
- TELEGRAM

Production: encrypted token fields должны шифроваться application-level ключом.

---

## 10. State transitions

Allowed:

```text
NEW -> PLANNED
NEW -> IN_PROGRESS
NEW -> WAITING

PLANNED -> IN_PROGRESS
PLANNED -> WAITING

IN_PROGRESS -> WAITING
IN_PROGRESS -> DONE
IN_PROGRESS -> POSTPONED
IN_PROGRESS -> ON_HOLD

WAITING -> IN_PROGRESS
WAITING -> DONE
WAITING -> POSTPONED

POSTPONED -> PLANNED
POSTPONED -> IN_PROGRESS

ON_HOLD -> PLANNED
ON_HOLD -> IN_PROGRESS

OVERDUE -> DONE
OVERDUE -> POSTPONED
OVERDUE -> IN_PROGRESS

any active -> CANCELLED
```

`NEW -> WAITING` добавлен явно: задача с `task_type=AWAITING` (например «Жду договор от Ивана завтра») обычно сразу попадает в статус `WAITING`, минуя `PLANNED`/`IN_PROGRESS`.

## 11. Overdue rule

Логически задача просрочена, если:

```text
status NOT IN (DONE, CANCELLED)
AND due_at IS NOT NULL
AND due_at < now()
```

Поле `status=OVERDUE` допустимо для UI/business workflow, но сервис не должен полагаться только на него.

## 12. Concurrency

`tasks.version` использовать для optimistic locking на обновлениях.

## 13. SQLite compatibility

Для SQLite:
- UUID → TEXT;
- JSONB → JSON/TEXT;
- arrays → JSON;
- timezone хранить ISO-8601 UTC strings.

Production schema ориентирована на PostgreSQL.
