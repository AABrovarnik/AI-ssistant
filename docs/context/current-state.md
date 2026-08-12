# Current state handoff

Дата обновления: `2026-08-12`

## Цель

- Завершить live-проверку Phase 5 и перейти к Phase 7 — Gmail Read Integration.

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
- Full suite на PostgreSQL: `27 passed, 2 skipped`.
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
- Полный Docker test suite: `27 passed, 2 skipped`; Ruff и mypy проходят.

## Phase 4

- Кандидат из свободного текста сохраняется в `SourceMessage` до подтверждения.
- Telegram показывает inline-кнопки «Создать / Изменить / Игнорировать».
- «Создать» создаёт задачу с исходным сообщением и idempotency key; повторное нажатие безопасно.
- «Игнорировать» помечает кандидат как `IGNORED`.
- «Изменить» переводит кандидата в режим ожидания и обновляет его следующим сообщением.
- «Изменить» у созданной задачи также ожидает следующий текст и обновляет задачу без показа UUID.
- Карточка задачи показывает статус, срок, приоритет, тип, исполнителя и описание.
- Кнопки позволяют отдельно изменить название, срок и статус; внутренние UUID скрыты из обычных списков.
- Добавлен отдельный статус `UNKNOWN_PARTY`, отображаемый как «Исполнитель/отправитель не известен».
- Для задач `DELEGATED/AWAITING` без распознанного имени этот статус назначается автоматически.
- Кнопка `👤 Исполнитель` ожидает имя следующим сообщением, сохраняет/переиспользует контакт и после назначения возвращает задачу в статус «Новая».
- История изменений сохраняется в `TaskEvent`, но показывается только по кнопке `📜 История`.
- Отображение и ввод сроков используют `TIMEZONE`; для текущего worker настроено `Europe/Moscow`.

## Phase 6 — reviews

- Утренний, вечерний и недельный read-only отчёты формируются worker только из PostgreSQL, без изменения задач.
- Утренний отчёт по умолчанию отправляется в `07:00 Europe/Moscow`, вечерний — в `19:00`.
- Недельный обзор отправляется в день `weekly_review_day` в вечернее время; значение `1` означает понедельник по ISO.
- Каждый отчёт содержит inline-кнопки перехода к сегодняшним, просроченным, делегированным и ожидаемым задачам.
- Отправка отчётов не требует комментария или подтверждения.
- `digest_deliveries` предотвращает повторную отправку одного типа отчёта за день после рестарта worker.

## Phase 5 — reminder engine

- Реализована детерминированная policy для P1/P2/P3/P4 по срокам задачи.
- Worker планирует reminders, не создавая дубли; изменение срока отменяет старые policy-reminders.
- Доставка учитывает quiet hours `22:00–07:00` и переносит напоминание на окончание тихих часов.
- Ошибки Telegram получают retry с backoff и завершаются статусом `FAILED` после трёх попыток.
- Просроченные задачи получают overdue reminders по policy; ручная кнопка `🔔 Напомнить` идемпотентна.
- Кнопка `😴 Отложить` и `POST /tasks/{id}/snooze` переносят активные reminders.

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
4. Довести сценарий «Изменить» и проверить назначение исполнителя через кнопку `👤 Исполнитель`.
5. Перейти к Phase 7: Gmail Read Integration с source idempotency и Telegram confirmation.
