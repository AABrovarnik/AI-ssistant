# Implementation Plan — AI Secretary v1.0

Работа ведётся фазами. Каждая фаза должна заканчиваться зелёными тестами и кратким changelog.

---

# Phase 0 — Bootstrap

## Цель

Создать воспроизводимый каркас проекта.

## Сделать

- Python 3.12 project.
- `pyproject.toml`.
- FastAPI.
- config via Pydantic Settings.
- structured logging.
- Dockerfile.
- docker-compose.
- PostgreSQL.
- Alembic.
- pytest.
- Ruff.
- mypy.
- `.gitignore`.
- `/health/live`.
- `/health/ready`.

## Acceptance

```bash
docker compose up -d --build
curl http://localhost:8000/health/live
pytest
```

Все проходят.

---

# Phase 1 — Database + Task Core

## Сделать

Модели:
- users;
- contacts;
- tasks;
- task_events;
- reminders;
- source_messages;
- task_sources;
- user_settings.

Services:
- create;
- update;
- complete;
- postpone;
- cancel;
- status transition;
- task views.

Добавить optimistic locking.

## Tests

- valid transitions;
- invalid transitions;
- overdue logic;
- version conflict;
- audit event;
- soft cancellation;
- duplicate source prevention.

## Acceptance

REST API позволяет полностью вести MY_TASK/DELEGATED/AWAITING без LLM.

---

# Phase 2 — Telegram Bot

## Сделать

- `/start`
- `/help`
- `/new`
- `/today`
- `/week`
- `/overdue`
- `/delegated`
- `/waiting`
- `/search`
- `/settings`

Inline buttons:
- Создать
- Изменить
- Выполнено
- Перенести
- Жду
- Отмена
- Напомнить

Owner whitelist по Telegram user id.

## Acceptance

Пользователь может управлять задачами через Telegram без прямого REST.

---

# Phase 3 — LLM Parsing

## Сделать

- classifier;
- task extractor;
- status analyzer;
- search parser;
- JSON schema validation;
- one repair retry;
- confidence policy;
- prompt injection isolation.

Подключить через abstraction `LLMProvider`.

## Acceptance

Фразы:

```text
Завтра до 15:00 подготовить смету.
Сергей должен до пятницы прислать расчёт.
Жду договор от Ивана завтра.
Иван прислал договор.
Что мне должен Иван?
```

дают корректные structured actions.

---

# Phase 4 — Confirmation UX

## Сделать

Preview карточка task candidate.

Кнопки:

- ✅ Создать
- ✏️ Изменить
- ❌ Игнорировать

Для неоднозначного assignee — выбор контакта.

Для неоднозначной задачи при completion — выбор из нескольких.

## Acceptance

LLM не выполняет внешнее действие напрямую.

---

# Phase 5 — Reminder Engine

## Сделать

- deterministic reminder policy;
- reminder records;
- scheduler;
- quiet hours;
- dedupe;
- retry;
- overdue scan;
- snooze.

## Acceptance

Из тестового deadline автоматически создаются правильные reminders.

Повторный scan не создаёт дубли.

---

# Phase 6 — Daily / Evening / Weekly Reviews

## Сделать

- morning digest;
- evening unresolved review;
- weekly review.

Telegram buttons для перехода к спискам.

## Acceptance

Digest создаётся из DB query, LLM только формулирует краткое резюме.

---

# Phase 7 — Gmail Read Integration

## MVP

- OAuth credentials;
- polling;
- fetch new messages;
- source message storage;
- classification;
- candidate generation;
- Telegram confirmation.

## Добавить фильтры

- VIP/TRUSTED/NORMAL/IGNORE;
- newsletter ignore;
- sender whitelist/blacklist.

## Acceptance

Тестовое письмо:

`Просим направить документы не позднее 14 августа`

создаёт candidate, но не task до подтверждения.

Повторный poll не создаёт duplicate.

---

# Phase 8 — Google Calendar

## Сделать

- connect OAuth account;
- create event;
- update event when task due changes;
- record event id;
- retry failed sync.

Calendar event создаётся:
- по explicit request;
- для встреч;
- для task с конкретным временем при включённой policy.

## Acceptance

Изменение срока task обновляет связанное событие.

---

# Phase 9 — End-to-End hardening

## E2E 1 — Delegation

1. User: `Поручил Сергею до пятницы получить расчёт`.
2. AI candidate.
3. Confirm.
4. DB task.
5. Reminder.
6. Deadline missed.
7. Overdue.
8. Suggest follow-up.
9. User: `Сергей прислал расчёт`.
10. DONE + event log.

## E2E 2 — Gmail

1. Email arrives.
2. Poll.
3. Candidate.
4. Telegram approval.
5. Task stored.
6. Source attached.

## E2E 3 — Waiting

1. Forward: `Хорошо, завтра пришлю договор`.
2. AWAITING candidate.
3. Confirm.
4. Deadline missed.
5. Follow-up suggestion.

---

# Phase 10 — Security + Operations

## Сделать

- auth;
- owner whitelist;
- secret management;
- DB not exposed;
- encrypted OAuth refresh token;
- rate limiting;
- sensitive log filter;
- backup script;
- restore script;
- log rotation;
- health checks.

## Acceptance

- `.env` not tracked;
- no secrets in logs/tests;
- backup restores into clean Postgres;
- dependency failure produces controlled error.

---

# Phase 11 — VPS Production Deployment

## Target

Ubuntu 24.04.

## Checklist

- Docker Engine installed.
- Firewall.
- SSH keys.
- non-root deployment user.
- project directory.
- `.env`.
- `docker compose up -d`.
- migration.
- Telegram webhook/polling strategy.
- backup cron.
- restart policy.
- time synchronization.
- disk monitoring.

## Final Definition of Done

AI Secretary v1.0 работает после reboot VPS без ручного запуска и проходит E2E smoke test.
