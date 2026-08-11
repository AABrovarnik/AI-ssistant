# Current state handoff

Дата обновления: `2026-08-11`

## Цель

- Реализовать Phase 4 — confirmation UX для LLM-кандидатов.

## Статус

- Phase 1 завершена и опубликована на GitHub.
- Phase 2 реализована локально; polling активен после ротации Telegram token.
- Phase 3 завершена; Phase 4 реализована локально, worker собран и тесты проходят.
- Reviewed dev pack хранится в `Posts/ai-secretary-v1.0-dev-pack/reviewed/`.

## Факты

- Добавлены Telegram Bot API client, long polling и owner whitelist.
- Реализованы `/start`, `/help`, `/new`, `/today`, `/week`, `/overdue`, `/delegated`, `/waiting`, `/search`, `/settings` и `/edit`.
- Добавлены inline actions: complete, edit prompt, postpone, waiting, cancel, reminder.
- Telegram unit tests используют fake client и не ходят во внешний API.
- Full suite на PostgreSQL: `21 passed, 2 skipped`.
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
- Полный Docker test suite: `21 passed, 2 skipped`; Ruff и mypy проходят.

## Phase 4

- Кандидат из свободного текста сохраняется в `SourceMessage` до подтверждения.
- Telegram показывает inline-кнопки «Создать / Изменить / Игнорировать».
- «Создать» создаёт задачу с исходным сообщением и idempotency key; повторное нажатие безопасно.
- «Игнорировать» помечает кандидат как `IGNORED`.
- «Изменить» пока запрашивает исправленный вариант отдельным сообщением; полноценное редактирование кандидата — следующий шаг.
- Сроки в Telegram отображаются в российском формате `ДД.ММ.ГГГГ ЧЧ:ММ`; внутренние UUID скрыты из обычных списков.

## Инфраструктурное условие

- OpenClaw `gateway.http.endpoints.chatCompletions.enabled` включён.
- Gateway привязан к `172.18.0.1` — Docker bridge проекта, не к wildcard-интерфейсам.
- UFW разрешает только `172.18.0.0/16 -> 172.18.0.1:18789/tcp`.
- Worker получает `OPENCLAW_API_KEY` runtime-only из Gateway token; token не записан в Git.
- Authenticated `/v1/models` из worker отвечает `200`; реальный parser smoke test прошёл.
- При пересоздании worker нужно снова передавать token через окружение, если он не добавлен вручную в локальный `.env`.

## Следующие шаги

1. Проверить в Telegram: `Завтра до 15:00 подготовить смету` и `Сергей должен до пятницы прислать расчёт`.
2. Проверить status preview: `Иван прислал договор`.
3. Проверить natural-language search: `Что мне должен Иван?`.
4. Довести сценарий «Изменить» и добавить уточнение/разрешение имени исполнителя.
