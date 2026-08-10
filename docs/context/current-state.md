# Current state handoff

Дата обновления: `2026-08-10`

## Цель

- Реализовать reviewed Phase 1 — Database + Task Core для AI Secretary v1.0.

## Статус

- `Phase 1 complete` локально.
- Полный reviewed dev pack хранится в `Posts/ai-secretary-v1.0-dev-pack/reviewed/`.
- Следующий этап: Phase 2 — Telegram Bot.

## Факты

- Добавлены users, contacts, tasks, source_messages, task_sources, task_events, reminders и user_settings.
- Task Core поддерживает MY_TASK, DELEGATED и AWAITING, переходы статусов, postpone, cancel, overdue views и optimistic version locking.
- Добавлены идемпотентные операции и защита от дублей source messages.
- Миграции: `20260809_01`, `20260809_02`, `20260810_03`.
- Acceptance suite на PostgreSQL: `9 passed`.
- Ruff и mypy проходят.
- Локальный HEAD: `a3e0031`; GitHub `origin/main` пока на `80cf4ae`.

## Решения

- Сохранён legacy wire format статусов в нижнем регистре для совместимости существующих API-клиентов.
- PostgreSQL используется как acceptance-контур; локальная aiosqlite-проверка в этом окружении зависает при открытии соединения.

## Открытые вопросы

- Отправить commit Phase 1 на `AABrovarnik/AI-ssistant:main` после подтверждения пользователя; потребуется passphrase SSH-ключа.
- Перед Phase 2 определить Telegram owner whitelist и способ polling/webhook.

## Следующие шаги

1. Отправить локальные commits на GitHub.
2. Спроектировать Telegram adapter и команды Phase 2.
3. Добавить Telegram integration tests без подключения LLM.
