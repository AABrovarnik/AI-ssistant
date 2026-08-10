# Current state handoff

Дата обновления: `2026-08-10`

## Цель

- Реализовать reviewed Phase 2 — Telegram Bot без LLM.

## Статус

- Phase 1 завершена и опубликована на GitHub.
- Phase 2 реализована локально; polling ожидает ротации Telegram token.
- Reviewed dev pack хранится в `Posts/ai-secretary-v1.0-dev-pack/reviewed/`.

## Факты

- Добавлены Telegram Bot API client, long polling и owner whitelist.
- Реализованы `/start`, `/help`, `/new`, `/today`, `/week`, `/overdue`, `/delegated`, `/waiting`, `/search`, `/settings` и `/edit`.
- Добавлены inline actions: complete, edit prompt, postpone, waiting, cancel, reminder.
- Telegram unit tests используют fake client и не ходят во внешний API.
- Full suite на PostgreSQL: `12 passed`.
- Ruff и mypy проходят.

## Безопасность и блокеры

- Старый Telegram token получил `401`, а исходная версия httpx logging записала URL с token в Docker logs.
- Worker остановлен; в локальном `.env` выставлен `TELEGRAM_MODE=disabled`.
- Token нужно отозвать/перевыпустить через BotFather. После замены вернуть `TELEGRAM_MODE=polling` и проверить `getUpdates`.
- Скомпрометированный token не записан в Git и не повторяется в handoff.

## Следующие шаги

1. Ротировать Telegram token и обновить локальный `.env`.
2. Вернуть `TELEGRAM_MODE=polling`, поднять worker и проверить Telegram smoke вручную.
3. Закоммитить Phase 2 и отправить commit на GitHub после проверки.
