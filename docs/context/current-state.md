# Current state handoff

Дата обновления: `2026-08-11`

## Цель

- Реализовать reviewed Phase 3 — LLM Parsing.

## Статус

- Phase 1 завершена и опубликована на GitHub.
- Phase 2 реализована локально; polling активен после ротации Telegram token.
- Phase 3 реализована локально и worker перезапущен с новым кодом.
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

## Phase 3

- Добавлены `LLMProvider`, OpenAI-compatible transport и `LLMService`.
- Реализованы classifier, task extractor, status analyzer и search parser.
- Ответы валидируются Pydantic; предусмотрен ровно один repair retry.
- Confidence ниже `0.65` принудительно переводит классификацию в `UNCLEAR`.
- Свободный текст Telegram показывает task candidate preview и пока не создаёт задачу в БД.
- `TASK_COMPLETE` и `STATUS_UPDATE` получают безопасный status preview без автоматического изменения БД.
- `INFORMATION` проходит через search parser, фильтрует задачи локально и возвращает результаты.
- Полный Docker test suite: `18 passed, 2 skipped`; Ruff и mypy проходят.

## Инфраструктурное условие

- OpenClaw должен иметь включённый `gateway.http.endpoints.chatCompletions.enabled`.
- `OPENCLAW_BASE_URL` должен быть адресом, доступным из worker-контейнера; `127.0.0.1` внутри контейнера указывает на сам worker.
- Если у Gateway включена token/password auth, заполнить `OPENCLAW_API_KEY`.
- До выполнения этих условий команды `/start`, `/help`, `/new` и inline actions продолжают работать, а свободный текст получает безопасное сообщение об ошибке parser.

## Следующие шаги

1. Включить и подключить OpenClaw endpoint к worker-сети.
2. Проверить в Telegram: `Завтра до 15:00 подготовить смету` и `Сергей должен до пятницы прислать расчёт`.
3. Перейти к Phase 4 — confirmation UX с кнопками «Создать / Изменить / Игнорировать».
