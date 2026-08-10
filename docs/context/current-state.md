# Current state handoff

Дата обновления: `2026-08-10`

## Цель

- Реализовать reviewed Phase 2 — Telegram Bot без LLM.

## Статус

- Phase 1 завершена и опубликована на GitHub.
- Phase 2 реализована локально; polling активен после ротации Telegram token.
- Reviewed dev pack хранится в `Posts/ai-secretary-v1.0-dev-pack/reviewed/`.

## Факты

- Добавлены Telegram Bot API client, long polling и owner whitelist.
- Реализованы `/start`, `/help`, `/new`, `/today`, `/week`, `/overdue`, `/delegated`, `/waiting`, `/search`, `/settings` и `/edit`.
- Добавлены inline actions: complete, edit prompt, postpone, waiting, cancel, reminder.
- Telegram unit tests используют fake client и не ходят во внешний API.
- Full suite на PostgreSQL: `12 passed`.
- Ruff и mypy проходят.

## Безопасность и блокеры

- Старый Telegram token был отозван/заменён после `401` и попадания URL в старые Docker logs.
- httpx request logging отключён для Telegram URL, а `401` прекращает polling без retry-loop.
- Новый token прошёл `getMe`; в локальном `.env` выставлен `TELEGRAM_MODE=polling`.
- Token не записан в Git и не повторяется в handoff.

## Следующие шаги

1. Проверить команды и inline actions в Telegram от owner account.
2. Закоммитить обновлённый handoff и отправить Phase 2 на GitHub.
3. Перейти к Phase 3 — LLM Parsing.
